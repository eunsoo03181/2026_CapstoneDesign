// nonverbal_charts.js
// 결과 화면용 — 비언어 지표 "어떻게 평가했는지" 설명 + 시간 추이 미니 차트.
// 외부 차트 라이브러리 없이 순수 SVG 로 그림.
//
// 사용:
//   import { renderNonverbalDetails } from '/static/nonverbal_charts.js';
//   renderNonverbalDetails(containerEl, nonverbalMetricsPayload);
//
// 입력 payload 형태 (서버 /api/sessions/{code} 또는 /api/interview/{sid}/result):
// {
//   ok: true,
//   duration_sec: 65.4,
//   score_20: 14.5,
//   scores_20: { smile:{score,max,label}, gaze:{score,max,label},
//                posture:{score,max,label}, _focus_component?, _blink_component? },
//   metrics: { smile_ratio, focus_ratio, blink_per_minute, avg_movement_px },
//   timeline: [{ t, smile_ratio, focus_ratio, blink_per_minute, avg_movement_px }, ...]
// }

const SERIES = [
  {
    key: "smile_ratio",
    label: "미소 비율",
    unit: "%",
    color: "#f59e0b",         // amber-500
    fillColor: "rgba(245,158,11,0.15)",
    fullAt: 20,                // 만점 기준선
    fullText: "20% 이상이면 만점",
    yMin: 0, yMax: 60,
    rule: "표정 안정성 6점. 미소가 감지된 프레임 비율을 누적해 측정하며, 면접 중 20% 이상 미소가 보이면 만점.",
    fmt: (v) => `${v.toFixed(1)}%`,
  },
  {
    key: "focus_ratio",
    label: "정면 응시 비율",
    unit: "%",
    color: "#6366f1",         // indigo-500
    fillColor: "rgba(99,102,241,0.15)",
    fullAt: 75,
    fullText: "75% 이상이면 응시 만점 (6점)",
    yMin: 0, yMax: 100,
    rule: "시선 안정성(10점) 중 응시 컴포넌트(6점). 동공이 화면 중앙을 향한 프레임 비율을 누적 측정.",
    fmt: (v) => `${v.toFixed(1)}%`,
  },
  {
    key: "blink_per_minute",
    label: "분당 깜빡임",
    unit: "회",
    color: "#06b6d4",         // cyan-500
    fillColor: "rgba(6,182,212,0.15)",
    fullAt: 17.5,
    fullText: "10~25회/분이면 깜빡임 만점 (4점)",
    yMin: 0, yMax: 40,
    rule: "시선 안정성(10점) 중 깜빡임 컴포넌트(4점). 10~25회/분이 자연스러운 구간, 너무 적거나 많으면 감점.",
    fmt: (v) => `${v.toFixed(1)}회`,
    bandLow: 10, bandHigh: 25,   // 만점 구간 음영
  },
  {
    key: "avg_movement_px",
    label: "평균 자세 이동",
    unit: "px/f",
    color: "#10b981",         // emerald-500
    fillColor: "rgba(16,185,129,0.15)",
    fullAt: 1.5,
    fullText: "1.5px/frame 이하면 만점 (4점)",
    yMin: 0, yMax: 6,
    rule: "자세 안정성 4점. 코끝 좌표의 프레임 간 평균 이동량으로 측정 — 작을수록 안정.",
    fmt: (v) => `${v.toFixed(2)}px`,
    invert: true,              // 작을수록 좋음 → 만점선 아래가 좋음
  },
];

/**
 * 컨테이너에 비언어 상세(평가 설명 + 시간 추이 차트)를 렌더링.
 * @param {HTMLElement} container
 * @param {Object} payload — nonverbal_metrics (ok=true 인 경우만 호출 권장)
 */
export function renderNonverbalDetails(container, payload) {
  if (!container) return;
  if (!payload || !payload.ok) {
    container.innerHTML = "";
    return;
  }

  const timeline = Array.isArray(payload.timeline) ? payload.timeline : [];
  const metrics = payload.metrics || {};
  const scores = payload.scores_20 || {};

  // 옛 세션 — timeline 없는 경우 안내 + 설명 카드만
  const hasTimeline = timeline.length >= 2;

  const explanation = `
    <div class="rounded-xl bg-slate-50 border border-slate-200 p-4 mb-4">
      <p class="text-sm font-semibold text-slate-700 mb-2">📐 이렇게 측정·평가했어요</p>
      <p class="text-xs text-slate-600 leading-relaxed">
        면접 중 MediaPipe 얼굴 인식이 매 프레임 4가지 지표를 누적합니다.
        총 20점은 <b>표정 안정성 6</b> + <b>시선 안정성 10</b> (응시 6 + 깜빡임 4 통합) +
        <b>자세 안정성 4</b> 로 구성됩니다.
        아래 그래프는 면접이 진행되는 동안 각 지표의 누적 평균이
        어떻게 변해 갔는지 보여줍니다 — 후반으로 갈수록 안정된 값에 수렴합니다.
      </p>
    </div>`;

  const chartsHtml = SERIES.map(s => {
    const finalVal = metrics[s.key];
    const finalFmt = (typeof finalVal === "number") ? s.fmt(finalVal) : "-";
    const chartSvg = hasTimeline ? buildLineChart(timeline, s) : emptyChartPlaceholder(s);
    return `
      <div class="rounded-xl border border-slate-200 bg-white p-4">
        <div class="flex items-baseline justify-between mb-1">
          <p class="text-sm font-semibold text-slate-800">${s.label}</p>
          <p class="text-sm font-bold tabular-nums" style="color:${s.color}">${finalFmt}</p>
        </div>
        <p class="text-[11px] text-slate-500 mb-2">${s.fullText}</p>
        ${chartSvg}
        <p class="text-[11px] text-slate-600 mt-2 leading-relaxed">${s.rule}</p>
      </div>`;
  }).join("");

  const note = hasTimeline
    ? ""
    : `<p class="text-[11px] text-slate-400 mt-3">
         ※ 이 면접은 시간 추이 데이터가 저장되지 않아 그래프 대신 최종 평균값만 표시됩니다 (옛 세션).
       </p>`;

  container.innerHTML = `
    ${explanation}
    <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">${chartsHtml}</div>
    ${note}
  `;
}

// ---------------------- 내부 헬퍼 ----------------------

function buildLineChart(timeline, s) {
  const W = 320, H = 110;
  const pad = { l: 30, r: 8, t: 8, b: 18 };
  const innerW = W - pad.l - pad.r;
  const innerH = H - pad.t - pad.b;

  const tMax = timeline[timeline.length - 1].t || 1;
  const yMin = s.yMin;
  const yMax = Math.max(s.yMax, ...timeline.map(p => p[s.key] || 0)) * 1.05;

  const xOf = (t) => pad.l + (t / tMax) * innerW;
  const yOf = (v) => pad.t + innerH - ((v - yMin) / (yMax - yMin)) * innerH;

  // 만점 기준선 좌표
  const fullY = yOf(s.fullAt);

  // 만점 구간 음영 (BPM 처럼 구간 만점인 지표)
  let bandRect = "";
  if (s.bandLow != null && s.bandHigh != null) {
    const y1 = yOf(s.bandHigh);
    const y2 = yOf(s.bandLow);
    bandRect = `<rect x="${pad.l}" y="${y1}" width="${innerW}" height="${y2 - y1}"
                      fill="${s.color}" opacity="0.08"/>`;
  }

  // 영역 polygon (라인 아래 음영)
  const areaPts = timeline.map(p => `${xOf(p.t)},${yOf(p[s.key] ?? 0)}`).join(" ");
  const areaPolygon = `${pad.l},${yOf(yMin)} ${areaPts} ${xOf(tMax)},${yOf(yMin)}`;

  // 라인 path
  const linePts = timeline.map(p => `${xOf(p.t)},${yOf(p[s.key] ?? 0)}`).join(" ");

  // Y 축 눈금 (3개)
  const yTicks = [yMin, (yMin + yMax) / 2, yMax];
  const yTickLines = yTicks.map(v => `
    <line x1="${pad.l}" y1="${yOf(v)}" x2="${W - pad.r}" y2="${yOf(v)}"
          stroke="#e2e8f0" stroke-width="1"/>
    <text x="${pad.l - 4}" y="${yOf(v) + 3}" text-anchor="end"
          font-size="9" fill="#94a3b8">${formatYTick(v)}</text>
  `).join("");

  // X 축 눈금 (시작/중간/끝)
  const xTicks = [0, tMax / 2, tMax];
  const xTickLabels = xTicks.map(t => `
    <text x="${xOf(t)}" y="${H - 4}" text-anchor="middle"
          font-size="9" fill="#94a3b8">${formatTimeSec(t)}</text>
  `).join("");

  return `
    <svg viewBox="0 0 ${W} ${H}" class="w-full h-auto" preserveAspectRatio="none"
         style="max-height:120px">
      ${bandRect}
      ${yTickLines}
      <polygon points="${areaPolygon}" fill="${s.fillColor}" stroke="none"/>
      <polyline points="${linePts}" fill="none" stroke="${s.color}" stroke-width="1.8"
                stroke-linejoin="round" stroke-linecap="round"/>
      <line x1="${pad.l}" y1="${fullY}" x2="${W - pad.r}" y2="${fullY}"
            stroke="${s.color}" stroke-width="1" stroke-dasharray="3 3" opacity="0.6"/>
      <text x="${W - pad.r - 2}" y="${fullY - 3}" text-anchor="end"
            font-size="9" fill="${s.color}" opacity="0.9">만점 기준</text>
      ${xTickLabels}
    </svg>
  `;
}

function emptyChartPlaceholder(s) {
  return `
    <div class="rounded-md bg-slate-50 border border-dashed border-slate-200 p-3 text-center">
      <p class="text-[11px] text-slate-400">시간 추이 데이터 없음</p>
    </div>
  `;
}

function formatYTick(v) {
  if (v >= 100) return v.toFixed(0);
  if (v >= 10)  return v.toFixed(0);
  return v.toFixed(1);
}

function formatTimeSec(sec) {
  if (sec < 60) return `${Math.round(sec)}s`;
  const m = Math.floor(sec / 60);
  const s = Math.round(sec % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}
