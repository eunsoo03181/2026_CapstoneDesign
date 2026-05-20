// face_analyzer.js
// 브라우저에서 MediaPipe.js 로 얼굴을 분석해 비언어 점수(20점)를 산출.
// nonverbal_analyzer.py 의 점수 체계와 동일 (6+6+4+4 = 20).

// MediaPipe Tasks Vision — 0.10.3 (공식 데모와 동일, jsdelivr WASM 정상 서빙)
import {
  FaceLandmarker,
  FilesetResolver,
} from "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.3";


// 점수 상한 (3축으로 통합)
//   표정 안정성    : 6
//   시선 안정성    : 10 (focus 6 + blink 4 통합)
//   자세 안정성    : 4
//   합계         : 20
const SMILE_MAX = 6.0;
const FOCUS_MAX = 6.0;     // 시선 안정성의 'focus' 컴포넌트
const BLINK_MAX = 4.0;     // 시선 안정성의 'blink' 컴포넌트
const GAZE_MAX  = 10.0;    // 시선 안정성 통합 (focus + blink)
const POSTURE_MAX = 4.0;

// 만점 기준 — 시선은 완화 (90% → 75%)
const SMILE_FULL_RATIO = 20.0;   // %
const FOCUS_FULL_RATIO = 75.0;   // % (이전 90 → 75 로 완화)
const BLINK_LOW = 10.0;
const BLINK_HIGH = 25.0;
const BLINK_CENTER = 17.5;
const POSTURE_FULL_PX = 1.5;

// 감지 임계값 — 시선 응시 판정 완화 (0.30 → 0.45)
const SMILE_BLENDSHAPE_THRESHOLD = 0.18;
const BLINK_BLENDSHAPE_THRESHOLD = 0.50;
const GAZE_OFFCENTER_THRESHOLD = 0.45;

// 타임라인 — 결과 화면 차트용으로 N초 간격으로 누적 지표 스냅샷
const TIMELINE_SAMPLE_SEC = 2.0;


export class FaceAnalyzer {
  constructor() {
    this.faceLandmarker = null;
    this.running = false;
    this.videoEl = null;
    this.canvasEl = null;
    this.canvasCtx = null;
    this.onStatus = null;   // 실시간 상태 콜백 ({smile, focus, blink, posture, faceDetected})

    // 누적 카운터
    this._reset();
  }

  _reset() {
    this.totalFrames = 0;
    this.smileFrames = 0;
    this.focusFrames = 0;
    this.blinkCount = 0;
    this.isBlinking = false;
    this.prevNose = null;
    this.totalMovement = 0;
    this.startTime = null;
    this.lastTimestamp = -1;
    // 타임라인: 결과 화면 차트용 누적 스냅샷 (TIMELINE_SAMPLE_SEC 간격)
    this.timeline = [];
    this.nextSampleAtSec = TIMELINE_SAMPLE_SEC;
  }

  /**
   * MediaPipe 모델 로드. 약 5MB 다운로드 (캐시되면 빠름).
   * 단계별로 자세한 에러를 던지도록 try/catch 분리.
   */
  async init() {
    if (this.faceLandmarker) return;

    console.log('[FaceAnalyzer] WASM fileset 로딩 시작...');
    let fileset;
    try {
      fileset = await FilesetResolver.forVisionTasks(
        "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.3/wasm"
      );
      console.log('[FaceAnalyzer] WASM fileset 로딩 완료');
    } catch (e) {
      const msg = (e && (e.message || e.type || e.toString())) || 'unknown';
      throw new Error(`WASM 로딩 실패: ${msg}`);
    }

    console.log('[FaceAnalyzer] 모델 로딩 시작...');
    try {
      this.faceLandmarker = await FaceLandmarker.createFromOptions(fileset, {
        baseOptions: {
          modelAssetPath:
            "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task",
          // GPU delegate 는 일부 환경(WebGL2 미지원/구형 GPU)에서 실패하므로 CPU 로 안정화
          delegate: "CPU",
        },
        outputFaceBlendshapes: true,
        runningMode: "VIDEO",
        numFaces: 1,
      });
      console.log('[FaceAnalyzer] 모델 로딩 완료 — 분석 준비됨');
    } catch (e) {
      const msg = (e && (e.message || e.type || e.toString())) || 'unknown';
      throw new Error(`모델 로딩 실패: ${msg}`);
    }
  }

  /**
   * <video> 엘리먼트 + (선택) <canvas> 를 받아 분석 루프 시작.
   * canvas 가 있으면 랜드마크를 그려서 시각화.
   */
  async start(videoEl, canvasEl = null, onStatus = null) {
    await this.init();
    this.videoEl = videoEl;
    this.canvasEl = canvasEl;
    this.canvasCtx = canvasEl ? canvasEl.getContext("2d") : null;
    this.onStatus = onStatus;
    this._reset();
    this.running = true;
    this.startTime = performance.now();
    requestAnimationFrame(() => this._tick());
  }

  stop() {
    this.running = false;
  }

  _tick() {
    if (!this.running) return;
    if (this.videoEl && this.videoEl.readyState >= 2) {
      const t = performance.now();
      if (t !== this.lastTimestamp) {
        this.lastTimestamp = t;
        try {
          const result = this.faceLandmarker.detectForVideo(this.videoEl, t);
          this._processResult(result);
          if (this.canvasCtx) this._draw(result);
        } catch (e) {
          // 검출 실패는 무시 (간헐적으로 발생)
        }
      }
    }
    requestAnimationFrame(() => this._tick());
  }

  _processResult(result) {
    const hasFace =
      result.faceLandmarks && result.faceLandmarks.length > 0;
    if (!hasFace) {
      if (this.onStatus) this.onStatus({ faceDetected: false });
      return;
    }

    const landmarks = result.faceLandmarks[0];
    const blendshapeArr =
      (result.faceBlendshapes && result.faceBlendshapes[0]
        && result.faceBlendshapes[0].categories) || [];
    const bs = {};
    for (const c of blendshapeArr) bs[c.categoryName] = c.score;

    this.totalFrames += 1;

    // -------- 미소 (blendshape 기반) --------
    const smile =
      ((bs.mouthSmileLeft || 0) + (bs.mouthSmileRight || 0)) / 2;
    const smiling = smile > SMILE_BLENDSHAPE_THRESHOLD;
    if (smiling) this.smileFrames += 1;

    // -------- 깜빡임 (blendshape) — 감→뜸 전이 시만 카운트 --------
    const eyeClose =
      ((bs.eyeBlinkLeft || 0) + (bs.eyeBlinkRight || 0)) / 2;
    const currentlyClosed = eyeClose > BLINK_BLENDSHAPE_THRESHOLD;
    if (currentlyClosed) {
      if (!this.isBlinking) {
        this.blinkCount += 1;
        this.isBlinking = true;
      }
    } else {
      this.isBlinking = false;
    }

    // -------- 시선 (eyeLookIn/Out/Up/Down blendshapes) --------
    const offCenter = Math.max(
      ((bs.eyeLookOutLeft || 0) + (bs.eyeLookOutRight || 0)) / 2,
      ((bs.eyeLookInLeft || 0) + (bs.eyeLookInRight || 0)) / 2,
      ((bs.eyeLookUpLeft || 0) + (bs.eyeLookUpRight || 0)) / 2,
      ((bs.eyeLookDownLeft || 0) + (bs.eyeLookDownRight || 0)) / 2
    );
    const focused = offCenter < GAZE_OFFCENTER_THRESHOLD;
    if (focused) this.focusFrames += 1;

    // -------- 자세 (코 끝 = landmark 1) 이동 누적 --------
    const nose = landmarks[1];
    if (this.prevNose) {
      // 정규화 좌표(0~1)를 의사 픽셀 거리로 환산 (640x480 기준)
      const dx = (nose.x - this.prevNose.x) * 640;
      const dy = (nose.y - this.prevNose.y) * 480;
      this.totalMovement += Math.sqrt(dx * dx + dy * dy);
    }
    this.prevNose = { x: nose.x, y: nose.y };

    if (this.onStatus) {
      this.onStatus({
        faceDetected: true,
        smiling,
        focused,
        blinking: currentlyClosed,
        smile_score: smile,
        eye_close: eyeClose,
        off_center: offCenter,
      });
    }

    // 타임라인 스냅샷 — TIMELINE_SAMPLE_SEC 간격으로 누적 지표 기록
    const elapsedSec = (performance.now() - this.startTime) / 1000;
    if (elapsedSec >= this.nextSampleAtSec) {
      const sr = (this.smileFrames / this.totalFrames) * 100;
      const fr = (this.focusFrames / this.totalFrames) * 100;
      const bpm = elapsedSec > 0 ? this.blinkCount / (elapsedSec / 60) : 0;
      const mv  = this.totalMovement / Math.max(1, this.totalFrames);
      this.timeline.push({
        t: round2(elapsedSec),
        smile_ratio:      round2(sr),
        focus_ratio:      round2(fr),
        blink_per_minute: round2(bpm),
        avg_movement_px:  round2(mv, 3),
      });
      this.nextSampleAtSec += TIMELINE_SAMPLE_SEC;
    }
  }

  _draw(result) {
    // 사용자에게는 카메라 영상만 보이도록 — 랜드마크 포인트 클라우드는 그리지 않음.
    // (canvas 자체는 비워둠. 디버깅 필요시 이 함수 안에서 직접 그리기 추가 가능)
    if (!this.canvasEl) return;
    const w = this.canvasEl.width;
    const h = this.canvasEl.height;
    this.canvasCtx.clearRect(0, 0, w, h);
  }

  /**
   * 누적 통계를 점수로 환산. nonverbal_analyzer.py 와 동일한 산식.
   */
  computeMetrics() {
    if (this.totalFrames < 30) {
      return {
        ok: false,
        reason: "데이터 부족 (얼굴 인식 시간이 너무 짧음)",
        score_20: 0.0,
      };
    }
    const durationSec = (performance.now() - this.startTime) / 1000;

    const smileRatio = (this.smileFrames / this.totalFrames) * 100;
    const focusRatio = (this.focusFrames / this.totalFrames) * 100;
    const bpm = durationSec > 0 ? this.blinkCount / (durationSec / 60) : 0;
    const avgMv = this.totalMovement / this.totalFrames;

    // 1) 표정 안정성 — 20% 이상 = 만점
    const smileScore = Math.min(SMILE_MAX, (smileRatio / SMILE_FULL_RATIO) * SMILE_MAX);
    // 2-a) 시선 (응시 비율) — FOCUS_FULL_RATIO(75%) 이상 = 만점
    const focusScore = Math.min(FOCUS_MAX, (focusRatio / FOCUS_FULL_RATIO) * FOCUS_MAX);
    // 2-b) 깜빡임 — 10~25 만점, 중앙(17.5)에서 거리 비례 감점
    const blinkScore =
      bpm >= BLINK_LOW && bpm <= BLINK_HIGH
        ? BLINK_MAX
        : Math.max(0, BLINK_MAX - Math.abs(bpm - BLINK_CENTER) * 0.1);
    // 2) 시선 안정성 — 응시 + 깜빡임 통합 (max 10)
    const gazeScore = Math.min(GAZE_MAX, focusScore + blinkScore);
    // 3) 자세 안정성 — 1.5px 이하 만점, 초과 픽셀당 2점 감점
    const postureScore = Math.max(
      0,
      POSTURE_MAX - Math.max(0, avgMv - POSTURE_FULL_PX) * 2.0
    );

    const score20 = smileScore + gazeScore + postureScore;

    return {
      ok: true,
      duration_sec: round2(durationSec),
      metrics: {
        smile_ratio: round2(smileRatio),
        focus_ratio: round2(focusRatio),
        blink_per_minute: round2(bpm),
        avg_movement_px: round2(avgMv, 3),
      },
      // 3축 (표정 안정성 / 시선 안정성 / 자세 안정성) = 6 + 10 + 4 = 20
      scores_20: {
        smile:   { score: round2(smileScore),   max: SMILE_MAX,   label: smileLabel(smileRatio) },
        gaze:    { score: round2(gazeScore),    max: GAZE_MAX,    label: gazeLabel(focusRatio, bpm) },
        posture: { score: round2(postureScore), max: POSTURE_MAX, label: postureLabel(avgMv) },
        // (내부용) 분해된 컴포넌트 — 디버깅·향후 호환용. UI 노출 X.
        _focus_component: { score: round2(focusScore), max: FOCUS_MAX, label: focusLabel(focusRatio) },
        _blink_component: { score: round2(blinkScore), max: BLINK_MAX, label: blinkLabel(bpm) },
      },
      score_20: round2(score20),
      // 결과 화면 차트용 — 누적 지표를 N초 간격으로 샘플링한 시계열
      timeline: this.timeline,
    };
  }
}

function round2(n, digits = 2) {
  const m = Math.pow(10, digits);
  return Math.round(n * m) / m;
}

function smileLabel(r) {
  if (r >= 20) return "안정";
  if (r >= 10) return "약간 부족";
  return "표정 거의 없음";
}
function focusLabel(r) {
  if (r >= 70) return "응시 안정";
  if (r >= 50) return "양호";
  return "시선 이탈 잦음";
}
function blinkLabel(bpm) {
  if (bpm >= 10 && bpm <= 25) return "안정";
  if (bpm > 25) return "긴장도 높음(과다 깜빡임)";
  return "응시 과다(깜빡임 적음)";
}
function gazeLabel(focusRatio, bpm) {
  const focusOk = focusRatio >= 70;
  const blinkOk = bpm >= 10 && bpm <= 25;
  if (focusOk && blinkOk) return "시선 안정";
  if (focusOk) return bpm > 25 ? "응시는 양호하나 깜빡임 다소 많음" : "응시는 양호하나 깜빡임 적음";
  if (blinkOk) return "깜빡임은 안정적이나 시선 이탈 다소 있음";
  return "시선 이탈·깜빡임 불안정";
}
function postureLabel(mv) {
  if (mv <= 1.5) return "안정";
  if (mv <= 3.0) return "약간 흔들림";
  return "산만함";
}
