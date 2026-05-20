"""
면접 중 표정·시선·자세를 분석해 비언어 점수(20점 만점)를 산출.

- InterviewAnalyzer    : 프레임 단위 분석 + 누적 통계 + 점수 환산
- BackgroundAnalyzer   : 별도 스레드에서 카메라를 돌리며 분석(메인 스레드 비차단)

원본 알고리즘은 그대로(미소/시선/깜빡임/자세 100점 합산).
최종 점수에 합치기 위해 100점을 5로 나눠 20점으로 환산해 제공한다.
"""

import math
import time
import threading
from collections import deque
from typing import Dict, Optional

import cv2
import mediapipe as mp
import numpy as np


class InterviewAnalyzer:
    """면접 비언어 행동 분석기.

    배점(20점 만점, 3축 통합 체계):
      - 표정 안정성  6점 : 미소 유지 비율 20% 이상 시 만점 (비례 감점)
      - 시선 안정성 10점 : focus(응시) + blink(깜빡임) 통합
                          · focus 6점 — 응시 비율 75% 이상 시 만점 (이전 90→75 완화)
                          · blink 4점 — 분당 10~25회 유지 시 만점
      - 자세 안정성  4점 : 프레임당 평균 움직임 1.5px 이하 시 만점

    내부적으로는 focus/blink 를 분리해 계산하되, 외부 노출용 scores_20 에는
    `smile / gaze / posture` 3축으로 통합한 결과만 제공.
    """

    # 항목별 만점 (분해 컴포넌트)
    SMILE_MAX = 6.0
    FOCUS_MAX = 6.0      # 시선 안정성의 'focus' 컴포넌트
    BLINK_MAX = 4.0      # 시선 안정성의 'blink' 컴포넌트
    GAZE_MAX  = 10.0     # 시선 안정성 통합 (focus + blink)
    POSTURE_MAX = 4.0
    TOTAL_MAX = 20.0

    # 만점 기준선 — 시선 완화 (이전 90% → 75%)
    SMILE_FULL_RATIO = 20.0   # 미소 유지 비율(%)
    FOCUS_FULL_RATIO = 75.0   # 정면 응시 비율(%) — 완화
    BLINK_LOW = 10.0          # 분당 깜빡임 정상 하한
    BLINK_HIGH = 25.0         # 정상 상한
    BLINK_CENTER = 17.5       # 정상 범위 중앙
    POSTURE_FULL_PX = 1.5     # 평균 픽셀 이동량 만점 기준

    def __init__(self):
        # --- 1. MediaPipe 초기화 ---
        self.mp_face_mesh = mp.solutions.face_mesh
        self.face_mesh = self.mp_face_mesh.FaceMesh(
            max_num_faces=1,
            refine_landmarks=True,
            min_detection_confidence=0.6,
            min_tracking_confidence=0.6,
        )
        self.mp_drawing = mp.solutions.drawing_utils
        self.mp_drawing_styles = mp.solutions.drawing_styles

        # --- 2. 캘리브레이션 ---
        self.state = "CALIBRATING"
        self.calib_start_time = time.time()
        self.calib_duration = 3.0
        self.calib_smile_data = []
        self.calib_ear_data = []
        self.calib_mouth_data = []
        self.base_smile = 0.0
        self.base_ear = 0.0
        self.base_mouth = 0.0
        self.SMILE_THRESHOLD = 0.0
        self.EAR_THRESHOLD = 0.0
        self.SPEAKING_THRESHOLD = 0.0

        # --- 3. 누적 통계 ---
        self.total_frames = 0
        self.non_speaking_frames = 0
        self.smile_frames = 0
        self.blink_count = 0
        self.focused_frames = 0

        # --- 4. 보조 상태 ---
        self.prev_nose_pos = None
        self.total_movement = 0.0
        self.is_blinking = False
        self.interview_start_time = None

        self.GAZE_MARGIN = 0.07
        self.last_spoken_time = 0.0
        self.SPEAKING_BUFFER = 0.6

        self.mouth_history = deque(maxlen=10)
        self.ACTIVITY_THRESHOLD = 0.008

        # 깜빡임 동안 시선 점수 유지를 위한 직전 상태 캐시
        # (눈을 감고 있는 짧은 구간에서 홍채 좌표가 튀어 시선이 '이탈'로 잘못
        #  잡히는 문제를 방지: 직전이 정면 응시였으면 깜빡이는 동안에도 정면으로 간주)
        self.was_focused_just_before_blink = False

    # --- 기하 유틸 ---
    def get_distance(self, p1, p2, w, h):
        return math.hypot((p1.x - p2.x) * w, (p1.y - p2.y) * h)

    def calculate_ear(self, eye_landmarks, w, h):
        v1 = self.get_distance(eye_landmarks[1], eye_landmarks[5], w, h)
        v2 = self.get_distance(eye_landmarks[2], eye_landmarks[4], w, h)
        h1 = self.get_distance(eye_landmarks[0], eye_landmarks[3], w, h)
        if h1 == 0:
            return 0
        return (v1 + v2) / (2.0 * h1)

    # --- 프레임 분석 ---
    def analyze_frame(self, frame):
        h, w, _ = frame.shape
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = self.face_mesh.process(rgb)

        status = {
            "smile": False, "gaze_stable": False, "speaking": False, "movement": 0.0,
            "raw_smile": 0.0, "raw_ear": 0.0, "raw_gaze": 0.0,
            "raw_mouth": 0.0, "mouth_act": 0.0,
        }

        if not results.multi_face_landmarks:
            return frame, status

        lms = results.multi_face_landmarks[0].landmark
        eye_dist = self.get_distance(lms[33], lms[263], w, h)

        # 미소 비율
        mouth_width = self.get_distance(lms[61], lms[291], w, h)
        smile_ratio = mouth_width / eye_dist if eye_dist > 0 else 0
        # 입 벌림 + 움직임
        mouth_open = self.get_distance(lms[13], lms[14], w, h)
        mouth_open_norm = mouth_open / eye_dist if eye_dist > 0 else 0
        self.mouth_history.append(mouth_open_norm)
        mouth_act = float(np.std(self.mouth_history)) if len(self.mouth_history) == 10 else 0.0
        # EAR (왼쪽 눈)
        left_eye = [33, 160, 158, 133, 153, 144]
        ear = self.calculate_ear([lms[i] for i in left_eye], w, h)

        status.update({
            "raw_smile": smile_ratio,
            "raw_mouth": mouth_open_norm,
            "mouth_act": mouth_act,
            "raw_ear": ear,
        })

        # [A] 캘리브레이션
        if self.state == "CALIBRATING":
            elapsed = time.time() - self.calib_start_time
            remain = int(math.ceil(self.calib_duration - elapsed))
            if remain > 0:
                self.calib_smile_data.append(smile_ratio)
                self.calib_ear_data.append(ear)
                self.calib_mouth_data.append(mouth_open_norm)
                cv2.putText(frame, "CALIBRATING...", (w // 2 - 150, h // 2 - 40),
                            cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 255, 255), 3)
                cv2.putText(frame, f"Neutral Face: {remain}s", (w // 2 - 120, h // 2 + 20),
                            cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)
            else:
                self.base_smile = float(np.mean(self.calib_smile_data)) if self.calib_smile_data else 0.0
                self.base_ear = float(np.mean(self.calib_ear_data)) if self.calib_ear_data else 0.0
                self.base_mouth = float(np.mean(self.calib_mouth_data)) if self.calib_mouth_data else 0.0
                self.SMILE_THRESHOLD = self.base_smile * 1.07
                self.EAR_THRESHOLD = self.base_ear * 0.70
                self.SPEAKING_THRESHOLD = self.base_mouth + 0.04
                self.state = "INTERVIEWING"
                self.interview_start_time = time.time()

        # [B] 면접 모드
        elif self.state == "INTERVIEWING":
            self.total_frames += 1

            # 1) 눈 깜빡임 (시선 보정 로직이 is_blinking에 의존하므로 가장 먼저 갱신)
            if ear < self.EAR_THRESHOLD:
                if not self.is_blinking:
                    self.blink_count += 1
                    self.is_blinking = True
            else:
                self.is_blinking = False

            # 2) 발화 감지
            is_active = (mouth_open_norm > self.SPEAKING_THRESHOLD) and (mouth_act > self.ACTIVITY_THRESHOLD)
            if is_active:
                self.last_spoken_time = time.time()
                is_speaking = True
            else:
                is_speaking = (time.time() - self.last_spoken_time < self.SPEAKING_BUFFER)
            status["speaking"] = is_speaking

            # 3) 미소 — 경청(무발화) 시에만 카운트 (입 움직임에 의한 안면 왜곡 회피)
            if not is_speaking:
                self.non_speaking_frames += 1
                if smile_ratio > self.SMILE_THRESHOLD:
                    self.smile_frames += 1
                    status["smile"] = True

            # 4) 시선 — 깜빡이는 동안에는 직전 상태를 유지(억울한 시선이탈 방지)
            iris = lms[468]
            eye_w = abs(lms[133].x - lms[33].x)
            if eye_w > 0:
                gaze_ratio = abs(iris.x - lms[33].x) / eye_w
                status["raw_gaze"] = gaze_ratio

                if not self.is_blinking:
                    # 눈을 뜨고 있을 때만 정면 응시 여부를 새로 판정
                    is_now_focused = (
                        (0.5 - self.GAZE_MARGIN) < gaze_ratio < (0.5 + self.GAZE_MARGIN)
                    )
                    if is_now_focused:
                        self.focused_frames += 1
                    self.was_focused_just_before_blink = is_now_focused
                    status["gaze_stable"] = is_now_focused
                else:
                    # 깜빡이는 중: 직전이 정면이었으면 그대로 정면으로 인정
                    if self.was_focused_just_before_blink:
                        self.focused_frames += 1
                        status["gaze_stable"] = True
                    else:
                        status["gaze_stable"] = False

            # 5) 자세 — 코 끝 흔들림
            curr_nose = np.array([lms[1].x * w, lms[1].y * h])
            if self.prev_nose_pos is not None:
                mv = float(np.linalg.norm(curr_nose - self.prev_nose_pos))
                self.total_movement += mv
                status["movement"] = mv
            self.prev_nose_pos = curr_nose

        # 시각화
        self.mp_drawing.draw_landmarks(
            frame, results.multi_face_landmarks[0],
            self.mp_face_mesh.FACEMESH_TESSELATION, None,
            self.mp_drawing_styles.get_default_face_mesh_tesselation_style(),
        )
        return frame, status

    # --- 정성 피드백 라벨 ---
    @staticmethod
    def _smile_label(smile_ratio: float) -> str:
        if smile_ratio >= 20:
            return "안정"
        if smile_ratio >= 10:
            return "약간 부족"
        return "표정 거의 없음"

    @staticmethod
    def _focus_label(focus_ratio: float) -> str:
        if focus_ratio >= 70:
            return "응시 안정"
        if focus_ratio >= 50:
            return "양호"
        return "시선 이탈 잦음"

    @staticmethod
    def _blink_label(bpm: float) -> str:
        if 10 <= bpm <= 25:
            return "안정"
        if bpm > 25:
            return "긴장도 높음(과다 깜빡임)"
        return "응시 과다(깜빡임 적음)"

    @classmethod
    def _gaze_label(cls, focus_ratio: float, bpm: float) -> str:
        focus_ok = focus_ratio >= 70
        blink_ok = cls.BLINK_LOW <= bpm <= cls.BLINK_HIGH
        if focus_ok and blink_ok:
            return "시선 안정"
        if focus_ok:
            return "응시는 양호하나 깜빡임 다소 많음" if bpm > cls.BLINK_HIGH \
                else "응시는 양호하나 깜빡임 적음"
        if blink_ok:
            return "깜빡임은 안정적이나 시선 이탈 다소 있음"
        return "시선 이탈·깜빡임 불안정"

    @staticmethod
    def _posture_label(avg_mv: float) -> str:
        if avg_mv <= 1.5:
            return "안정"
        if avg_mv <= 3.0:
            return "약간 흔들림"
        return "산만함"

    # --- 점수 환산 (20점 직접 부여) ---
    def compute_metrics(self) -> Dict:
        """누적 통계를 점수로 환산. 20점 만점(6+6+4+4) + 원시 지표 + 라벨."""
        if self.state != "INTERVIEWING" or self.total_frames == 0:
            return {
                "ok": False,
                "reason": "데이터 부족 (캘리브레이션 미완료 또는 분석 프레임 0)",
                "score_20": 0.0,
            }

        duration = time.time() - self.interview_start_time
        silent_sec = (self.non_speaking_frames / self.total_frames) * duration
        speak_sec = duration - silent_sec

        smile_ratio = (self.smile_frames / self.non_speaking_frames * 100) if self.non_speaking_frames > 0 else 0
        focus_ratio = (self.focused_frames / self.total_frames) * 100
        bpm = (self.blink_count / (duration / 60)) if duration > 0 else 0
        avg_mv = self.total_movement / self.total_frames

        # 1) 표정 안정성 6점: 20% 이상이면 만점, 비례 감점
        smile_score = min(self.SMILE_MAX,
                          (smile_ratio / self.SMILE_FULL_RATIO) * self.SMILE_MAX)

        # 2-a) 시선 응시 (분해 컴포넌트) 6점: FOCUS_FULL_RATIO(75%) 이상 만점
        focus_score = min(self.FOCUS_MAX,
                          (focus_ratio / self.FOCUS_FULL_RATIO) * self.FOCUS_MAX)

        # 2-b) 깜빡임 (분해 컴포넌트) 4점: 10~25/min 만점
        if self.BLINK_LOW <= bpm <= self.BLINK_HIGH:
            blink_score = self.BLINK_MAX
        else:
            blink_score = max(0.0, self.BLINK_MAX - abs(bpm - self.BLINK_CENTER) * 0.1)

        # 2) 시선 안정성 10점 (통합) = focus + blink
        gaze_score = min(self.GAZE_MAX, focus_score + blink_score)

        # 3) 자세 안정성 4점: 1.5px/f 이하 만점, 초과 시 픽셀당 2점 감점
        posture_score = max(0.0, self.POSTURE_MAX - max(0.0, avg_mv - self.POSTURE_FULL_PX) * 2.0)

        total_20 = smile_score + gaze_score + posture_score

        return {
            "ok": True,
            "duration_sec": round(duration, 2),
            "silent_sec": round(silent_sec, 2),
            "speak_sec": round(speak_sec, 2),
            "metrics": {
                "smile_ratio": round(smile_ratio, 2),
                "focus_ratio": round(focus_ratio, 2),
                "blink_per_minute": round(bpm, 2),
                "avg_movement_px": round(avg_mv, 3),
            },
            # 외부 노출 3축: 표정 안정성 / 시선 안정성 / 자세 안정성
            "scores_20": {
                "smile":   {"score": round(smile_score, 2),   "max": self.SMILE_MAX,
                            "label": self._smile_label(smile_ratio)},
                "gaze":    {"score": round(gaze_score, 2),    "max": self.GAZE_MAX,
                            "label": self._gaze_label(focus_ratio, bpm)},
                "posture": {"score": round(posture_score, 2), "max": self.POSTURE_MAX,
                            "label": self._posture_label(avg_mv)},
                # (내부 디버깅용) 분해 컴포넌트
                "_focus_component": {"score": round(focus_score, 2), "max": self.FOCUS_MAX,
                                     "label": self._focus_label(focus_ratio)},
                "_blink_component": {"score": round(blink_score, 2), "max": self.BLINK_MAX,
                                     "label": self._blink_label(bpm)},
            },
            "score_20": round(total_20, 2),
        }

    def generate_report(self) -> str:
        m = self.compute_metrics()
        if not m["ok"]:
            return m["reason"]
        s = m["scores_20"]
        sc = m["metrics"]
        return (
            f"\n{'='*64}\n"
            f"            [ AI 면접 비언어 종합 평가 (20점 만점, 3축 통합) ]\n"
            f"{'='*64}\n"
            f" ▶ 비언어 점수    :  {m['score_20']:>5.2f} / 20.00\n"
            f" ▶ 진행 시간      :  {m['duration_sec']:.1f}초  "
            f"(침묵 {m['silent_sec']:.1f}s / 발화 {m['speak_sec']:.1f}s)\n"
            f"{'-'*64}\n"
            f" 1. 표정 안정성  {sc['smile_ratio']:>6.1f} %     "
            f"{s['smile']['score']:>4.2f} / {s['smile']['max']:.0f}   [{s['smile']['label']}]\n"
            f" 2. 시선 안정성  응시 {sc['focus_ratio']:>5.1f}% / 깜빡임 {sc['blink_per_minute']:>4.1f}/min  "
            f"{s['gaze']['score']:>4.2f} / {s['gaze']['max']:.0f}  [{s['gaze']['label']}]\n"
            f" 3. 자세 안정성  {sc['avg_movement_px']:>6.2f} px/f  "
            f"{s['posture']['score']:>4.2f} / {s['posture']['max']:.0f}   [{s['posture']['label']}]\n"
            f"{'='*64}\n"
        )

    # --- 카메라 루프 ---
    def run_camera_loop(
        self,
        show_window: bool = True,
        mirror: bool = True,
        stop_flag: Optional[threading.Event] = None,
    ) -> Dict:
        """카메라 루프 직접 실행. ESC 또는 stop_flag 로 종료. 종료 시 metrics 반환."""
        cap = cv2.VideoCapture(0)
        try:
            while cap.isOpened():
                if stop_flag is not None and stop_flag.is_set():
                    break
                ret, frame = cap.read()
                if not ret:
                    break
                if mirror:
                    frame = cv2.flip(frame, 1)
                frame, _ = self.analyze_frame(frame)
                if show_window:
                    cv2.imshow("AI Interview System", frame)
                    if cv2.waitKey(1) & 0xFF == 27:
                        break
        finally:
            cap.release()
            if show_window:
                cv2.destroyAllWindows()
        return self.compute_metrics()


class BackgroundAnalyzer:
    """별도 스레드로 InterviewAnalyzer 카메라 루프를 돌린다.

    macOS는 cv2.imshow를 메인 스레드에서만 안정적으로 띄울 수 있어서,
    백그라운드 모드는 기본적으로 imshow 없이 무음 분석한다.
    """

    def __init__(self, show_window: bool = False):
        self.analyzer = InterviewAnalyzer()
        self._stop_flag = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._show_window = show_window
        self._final_metrics: Optional[Dict] = None

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        self._final_metrics = self.analyzer.run_camera_loop(
            show_window=self._show_window, stop_flag=self._stop_flag
        )

    def stop(self, timeout: float = 5.0) -> Dict:
        self._stop_flag.set()
        if self._thread:
            self._thread.join(timeout=timeout)
        return self._final_metrics or self.analyzer.compute_metrics()


if __name__ == "__main__":
    print("3초 캘리브레이션 후 분석 시작합니다. 종료: ESC")
    a = InterviewAnalyzer()
    metrics = a.run_camera_loop(show_window=True)
    print(a.generate_report())
    import json
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
