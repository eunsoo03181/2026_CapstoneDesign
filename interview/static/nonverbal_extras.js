// nonverbal_extras.js
// 음성 비언어 카드 + 답변 일관성 검증 카드 — 결과·세션상세·분석 3화면 공통 렌더러.
//
// 사용:
//   import { renderVoiceCard, renderConsistencyCard } from '/static/nonverbal_extras.js';
//   renderVoiceCard(cardEl, voiceEvalPayload, voicePerQuestion);
//   renderConsistencyCard(cardEl, consistencyChecks, qaPairs);
//
// voiceEvalPayload 형태:
//   { ok, voice_nonverbal_total, max_score, per_question_count, average_metrics }
// voicePerQuestion: [{ok, voice_nonverbal_total, detail_scores, metrics}, ...]
// consistencyChecks: [{level, summary, issues:[{type,evidence,reason,recommended_question}], question_index}, ...]
// qaPairs (선택): result.html 의 [(q,a), ...] 또는 session_detail/analysis 의 questions 배열

function escapeHtml(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  })[c]);
}

function getQuestionText(qaPairs, idx) {
  const x = qaPairs?.[idx];
  if (!x) return `Q${idx + 1}`;
  // [q, a] 형태 또는 { question, text } 형태 모두 지원
  if (Array.isArray(x)) {
    return x[0]?.question || x[0]?.text || `Q${idx + 1}`;
  }
  return x.question || x.text || `Q${idx + 1}`;
}

export function renderVoiceCard(cardEl, voiceEval, voicePerQuestion = []) {
  if (!cardEl) return;
  if (!voiceEval || !voiceEval.ok) {
    cardEl.classList.add("hidden");
    cardEl.innerHTML = "";
    return;
  }
  cardEl.classList.remove("hidden");
  const am = voiceEval.average_metrics || {};
  // 첫 유효 답변의 detail_scores 를 대표로 (점수 평균은 voice_nonverbal_total)
  const perQ = (voicePerQuestion || []).filter(x => x && x.ok);
  const detail = perQ[0]?.detail_scores || {};
  const row = (key, label, max) => {
    const d = detail[key] || {};
    return `
      <div class="bg-slate-50 rounded-lg p-3">
        <p class="text-xs font-semibold text-slate-500 mb-1">${label} · ${d.score ?? "-"} / ${max} — ${escapeHtml(d.level || "")}</p>
        <p class="text-xs text-slate-700">${escapeHtml(d.comment || "")}</p>
      </div>`;
  };
  cardEl.innerHTML = `
    <div class="flex items-center justify-between mb-3">
      <h3 class="font-bold">음성 비언어 분석</h3>
      <span class="text-xs text-slate-400">유효 답변 ${voiceEval.per_question_count ?? 0}개 평균 · ${(voiceEval.voice_nonverbal_total ?? 0).toFixed(2)} / 10</span>
    </div>
    <p class="text-sm text-slate-600 mb-3">
      답변 음성에서 말 속도·침묵·반복어를 측정합니다. 거짓·긴장 같은 주관 해석은 하지 않고,
      <b>전달의 안정감</b>만 객관 수치로 평가합니다.
    </p>
    <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-3">
      ${row("speech_rate",       "말 속도",        3)}
      ${row("pause",             "침묵·끊김",      3)}
      ${row("filler_repetition", "반복어·더듬음",  2)}
      ${row("voice_stability",   "전달 안정성",    2)}
    </div>
    <div class="text-[11px] text-slate-500 grid grid-cols-2 sm:grid-cols-5 gap-2">
      <div>평균 WPM <b class="text-slate-700">${(am.words_per_minute ?? 0).toFixed(0)}</b></div>
      <div>침묵 횟수 <b class="text-slate-700">${(am.pause_count ?? 0).toFixed(1)}</b></div>
      <div>filler <b class="text-slate-700">${(am.filler_count ?? 0).toFixed(1)}</b></div>
      <div>반복 <b class="text-slate-700">${(am.repetition_count ?? 0).toFixed(1)}</b></div>
      <div>평균 길이 <b class="text-slate-700">${(am.duration_sec ?? 0).toFixed(1)}s</b></div>
    </div>`;
}

export function renderConsistencyCard(cardEl, consistencyChecks = [], qaPairs = []) {
  if (!cardEl) return;
  // 표시할 이슈가 하나도 없으면 카드 숨김 (모두 "없음" + issues 0)
  const visible = (consistencyChecks || []).filter(c =>
    (c.issues || []).length > 0 || (c.level && c.level !== "없음")
  );
  if (!visible.length) {
    cardEl.classList.add("hidden");
    cardEl.innerHTML = "";
    return;
  }
  cardEl.classList.remove("hidden");

  const levelBadge = (lv) => {
    const m = {
      "없음": "bg-emerald-50 text-emerald-700 border-emerald-200",
      "낮음": "bg-amber-50 text-amber-700 border-amber-200",
      "보통": "bg-orange-50 text-orange-700 border-orange-200",
      "높음": "bg-rose-50 text-rose-700 border-rose-200",
    };
    return `<span class="inline-block px-2 py-0.5 rounded-full text-[11px] font-bold border ${m[lv] || m["없음"]}">일관성 ${escapeHtml(lv || "없음")}</span>`;
  };

  const items = consistencyChecks.map((c, idx) => {
    if (!c.issues?.length && c.level === "없음") return "";
    const qText = getQuestionText(qaPairs, idx);
    const issues = (c.issues || []).map(it => `
      <li class="text-xs text-slate-700 leading-relaxed">
        <span class="text-slate-500 font-semibold">${escapeHtml(it.type || "확인 필요")}:</span>
        ${escapeHtml(it.reason || "")}
        ${it.evidence ? `<div class="text-[11px] text-slate-400 mt-0.5">근거: "${escapeHtml(it.evidence)}"</div>` : ""}
        ${it.recommended_question ? `<div class="text-[11px] text-indigo-700 mt-0.5">추천 후속 질문: ${escapeHtml(it.recommended_question)}</div>` : ""}
      </li>
    `).join("");
    return `
      <div class="border border-slate-200 rounded-lg p-3 mb-2">
        <div class="flex items-center gap-2 mb-1.5 flex-wrap">
          ${levelBadge(c.level || "없음")}
          <span class="text-xs font-semibold text-slate-700">Q${idx + 1}</span>
          <span class="text-xs text-slate-500 truncate">${escapeHtml(qText).slice(0, 80)}</span>
        </div>
        ${c.summary ? `<p class="text-xs text-slate-600 mb-1.5">${escapeHtml(c.summary)}</p>` : ""}
        ${issues ? `<ul class="space-y-1 list-disc list-inside">${issues}</ul>` : ""}
      </div>`;
  }).join("");

  cardEl.innerHTML = `
    <div class="flex items-center justify-between mb-3">
      <h3 class="font-bold">답변 일관성 검증</h3>
      <span class="text-xs text-slate-400">이력서·자기소개서·이전 답변 ↔ 현재 답변 비교</span>
    </div>
    <p class="text-sm text-slate-600 mb-3">
      자료와 답변 사이에서 <b>면접관이 추가 확인할 만한 포인트</b> 만 알려드립니다.
      거짓·진위 판단은 하지 않으며, 점수에는 반영되지 않습니다.
    </p>
    ${items}`;
}

// ─────────────────────────────────────────────────────────────────────
// 회사·직무 리서치 카드
// 사용자가 "회사 검색이 실제로 동작했는지" 확인할 수 있도록 LLM 이 만든
// company_job_summary 의 핵심 필드를 그대로 노출.
// ─────────────────────────────────────────────────────────────────────
export function renderCompanyResearchCard(cardEl, companyResearch) {
  if (!cardEl) return;
  const cr = companyResearch || {};
  const mode = cr.research_mode || "none";
  const summary = (cr.company_job_summary || {}).company_job_summary
                  || cr.company_job_summary
                  || {};
  // research_mode === 'none' 이고 summary 도 없으면 카드 숨김 (사용자가 회사 입력 안 함)
  if (mode === "none" && !cr.company_name && !cr.job_title && (!summary || !Object.keys(summary).length)) {
    cardEl.classList.add("hidden");
    cardEl.innerHTML = "";
    return;
  }
  cardEl.classList.remove("hidden");

  const modeBadge = (() => {
    if (mode === "auto") {
      return `<span class="inline-block px-2 py-0.5 rounded-full text-[11px] font-bold border bg-emerald-50 text-emerald-700 border-emerald-200">
                🔍 LLM 자동 리서치
              </span>`;
    }
    if (mode === "pasted") {
      return `<span class="inline-block px-2 py-0.5 rounded-full text-[11px] font-bold border bg-indigo-50 text-indigo-700 border-indigo-200">
                📋 사용자 입력 텍스트 요약
              </span>`;
    }
    return `<span class="inline-block px-2 py-0.5 rounded-full text-[11px] font-bold border bg-slate-50 text-slate-600 border-slate-200">
              회사 정보 없음
            </span>`;
  })();

  const company = escapeHtml(cr.company_name || "(미입력)");
  const job = escapeHtml(cr.job_title || "(미입력)");

  // summary 내부 필드들 — 있는 것만 표시
  const valToText = (v) => {
    if (v == null) return "";
    if (Array.isArray(v)) return v.map(x => String(x).trim()).filter(Boolean).join(" · ");
    return String(v).trim();
  };
  const field = (label, key) => {
    const text = valToText(summary[key]);
    if (!text || text === "확인 불가") return "";
    return `
      <div class="border-l-2 border-slate-200 pl-3 py-1">
        <p class="text-[11px] font-semibold text-slate-500 uppercase tracking-wide mb-0.5">${label}</p>
        <p class="text-sm text-slate-800 leading-relaxed">${escapeHtml(text)}</p>
      </div>`;
  };

  // 시드/압박 포인트 (있으면)
  const seedsHtml = (() => {
    const seeds = summary.question_seed_points || [];
    if (!seeds.length) return "";
    const items = seeds.slice(0, 6).map(s => {
      const topic = typeof s === "object" ? (s.topic || "") : String(s);
      return topic.trim() ? `<li class="text-xs text-slate-700">${escapeHtml(topic)}</li>` : "";
    }).filter(Boolean).join("");
    return items ? `
      <div class="border-l-2 border-indigo-200 pl-3 py-1">
        <p class="text-[11px] font-semibold text-indigo-600 uppercase tracking-wide mb-1">면접 시드 포인트</p>
        <ul class="list-disc list-inside space-y-0.5">${items}</ul>
      </div>` : "";
  })();

  const pressuresHtml = (() => {
    const ps = summary.pressure_points || [];
    if (!ps.length) return "";
    const items = ps.slice(0, 5).map(p => {
      const text = typeof p === "object" ? (p.point || p.topic || "") : String(p);
      return text.trim() ? `<li class="text-xs text-slate-700">${escapeHtml(text)}</li>` : "";
    }).filter(Boolean).join("");
    return items ? `
      <div class="border-l-2 border-rose-200 pl-3 py-1">
        <p class="text-[11px] font-semibold text-rose-600 uppercase tracking-wide mb-1">검증 포인트</p>
        <ul class="list-disc list-inside space-y-0.5">${items}</ul>
      </div>` : "";
  })();

  // summary 가 비어있는 경우 안내
  const hasAnyContent = Object.keys(summary || {}).some(k => valToText(summary[k]));
  const emptyNotice = !hasAnyContent ? `
    <p class="text-xs text-slate-500 mt-2">
      회사 리서치가 비어 있습니다. 회사명·직무명만으로는 LLM 이 정보를 찾지 못했거나,
      해당 면접 시점엔 리서치 기능이 비활성화돼 있었을 수 있어요.
    </p>` : "";

  cardEl.innerHTML = `
    <div class="flex items-center justify-between mb-3 flex-wrap gap-2">
      <h3 class="font-bold">회사·직무 리서치</h3>
      ${modeBadge}
    </div>
    <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-3">
      <div class="rounded-lg bg-slate-50 p-3">
        <p class="text-[11px] font-semibold text-slate-500 uppercase tracking-wide mb-0.5">회사</p>
        <p class="text-sm font-semibold text-slate-900">${company}</p>
      </div>
      <div class="rounded-lg bg-slate-50 p-3">
        <p class="text-[11px] font-semibold text-slate-500 uppercase tracking-wide mb-0.5">직무</p>
        <p class="text-sm font-semibold text-slate-900">${job}</p>
      </div>
    </div>
    <div class="space-y-2">
      ${field("회사 개요",          "company_overview")}
      ${field("주요 사업",          "main_business")}
      ${field("최근 전략",          "recent_strategy")}
      ${field("직무 역할",          "job_role")}
      ${field("직무 ↔ 사업 연관성", "business_relevance_to_job")}
      ${field("필요 역량",          "required_competencies")}
      ${field("필요 지식",          "required_knowledge")}
      ${field("관련 기술",          "required_technologies")}
      ${field("업무 환경",          "work_context")}
      ${field("핵심가치 / 인재상",   "core_values")}
      ${field("면접 키워드",         "interview_keywords")}
      ${seedsHtml}
      ${pressuresHtml}
    </div>
    ${emptyNotice}
    <p class="text-[11px] text-slate-400 mt-3">
      ${mode === "auto"
        ? "회사명만 입력된 경우 LLM 이 자동으로 공개 정보를 정리한 결과입니다 — 실제 회사 공식 자료와 다를 수 있어요."
        : mode === "pasted"
          ? "사용자가 붙여넣은 JD/회사 설명 텍스트를 LLM 이 정리한 결과입니다."
          : ""}
    </p>`;
}
