/**
 * 모든 페이지에 공통으로 끼워넣는 작은 스크립트:
 *   1) /auth/me 결과 role=admin 이면 nav 에 "관리" 버튼 추가
 *   2) impersonating=true 면 화면 상단에 노란 배너 + "관리자로 복귀" 버튼
 *
 * 사용법: <script src="/static/admin_chrome.js" defer></script>
 */
(async function () {
  let me;
  try {
    const res = await fetch('/auth/me', { credentials: 'same-origin' });
    if (!res.ok) return;
    me = await res.json();
  } catch (e) {
    return;
  }
  if (!me) return;

  // ---------- 00) 이메일 미인증 경고 배너 (로컬 가입자 + email_verified=false) ----------
  if (me.auth_provider === 'local' && me.email_verified === false) {
    const banner = document.createElement('div');
    banner.id = 'globalEmailVerifyBanner';
    banner.className = 'bg-amber-100 border-b border-amber-300 text-amber-900 text-sm sticky top-0 z-50';
    banner.innerHTML = `
      <div class="max-w-6xl mx-auto px-6 py-2 flex items-center justify-between gap-3 flex-wrap">
        <span>
          ✉️ <b>이메일 인증이 필요해요.</b>
          가입 시 받은 메일의 링크를 클릭해 인증을 완료해야 면접을 시작할 수 있어요.
        </span>
        <button id="resendVerifyBtn"
                class="px-3 py-1 rounded-md bg-amber-600 hover:bg-amber-700 text-white text-xs font-semibold whitespace-nowrap">
          인증 메일 재발송
        </button>
      </div>`;
    document.body.insertBefore(banner, document.body.firstChild);

    document.getElementById('resendVerifyBtn').addEventListener('click', async (e) => {
      const btn = e.currentTarget;
      btn.disabled = true; btn.textContent = '발송 중...';
      try {
        const r = await fetch('/auth/resend-verification', {
          method: 'POST', credentials: 'same-origin',
        });
        if (!r.ok) {
          const err = await r.json().catch(() => ({}));
          throw new Error(err.detail || '발송 실패');
        }
        const data = await r.json();
        btn.textContent = data.fallback_console ? '서버 콘솔 확인' : '✓ 발송 완료';
        btn.classList.remove('bg-amber-600','hover:bg-amber-700');
        btn.classList.add('bg-emerald-600');
      } catch (err) {
        alert('재발송 실패: ' + (err.message || err));
        btn.disabled = false; btn.textContent = '인증 메일 재발송';
      }
    });
  }

  // ---------- 0) 모든 로그인 사용자에게 "Credit" 잔액 chip + 친절한 tooltip ----------
  // /auth/me 응답의 credits / credits_unlimited 를 헤더 nav 앞에 표시.
  // 일반 사용자: '⚡ 12 크레딧' / admin·moderator: '⚡ 무제한'
  document.querySelectorAll('nav').forEach((nav) => {
    if (nav.querySelector('[data-credit-chip-wrap]')) return;

    // wrapper — hover tooltip 의 anchor 역할 (position: relative)
    const wrap = document.createElement('span');
    wrap.dataset.creditChipWrap = '1';
    wrap.className = 'relative inline-block';

    const chip = document.createElement('span');
    chip.dataset.creditChip = '1';
    const isLow = !me.credits_unlimited && (me.credits || 0) <= 0;
    chip.className = [
      'inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-semibold border cursor-help',
      me.credits_unlimited
        ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
        : isLow
          ? 'bg-rose-50 text-rose-700 border-rose-200 animate-pulse'
          : 'bg-indigo-50 text-indigo-700 border-indigo-200',
    ].join(' ');
    chip.innerHTML = me.credits_unlimited
      ? `<svg class="w-3.5 h-3.5" fill="currentColor" viewBox="0 0 20 20"><path d="M11 3a1 1 0 10-2 0v1H5a1 1 0 100 2h4v3H6a1 1 0 100 2h3v3a1 1 0 102 0v-3h3a1 1 0 100-2h-3V6h4a1 1 0 100-2h-4V3z"/></svg>
                무제한`
      : `<svg class="w-3.5 h-3.5" fill="currentColor" viewBox="0 0 20 20"><path d="M11.3 1.046A1 1 0 0112 2v5h4a1 1 0 01.82 1.573l-7 10A1 1 0 018 18v-5H4a1 1 0 01-.82-1.573l7-10a1 1 0 011.12-.38z"/></svg>
                ${me.credits ?? 0} 크레딧`;

    // Hover tooltip — 사용자 친화적 (전문용어 X, 짧고 명확)
    const tooltip = document.createElement('div');
    tooltip.className = [
      'hidden absolute right-0 top-full mt-2 w-64 z-50',
      'bg-white border border-slate-200 rounded-xl shadow-lg p-3 text-left',
    ].join(' ');
    if (me.credits_unlimited) {
      tooltip.innerHTML = `
        <p class="text-sm font-bold text-slate-800 mb-1">⚡ 크레딧 무제한</p>
        <p class="text-xs text-slate-600 leading-relaxed">
          관리자 계정은 크레딧을 사용하지 않고 자유롭게 면접을 만들 수 있어요.
        </p>`;
    } else if (isLow) {
      tooltip.innerHTML = `
        <p class="text-sm font-bold text-rose-700 mb-1">크레딧이 없어요</p>
        <p class="text-xs text-slate-600 leading-relaxed">
          면접 한 번 만들 때 <b>크레딧 1개</b> 가 필요해요.
          남은 크레딧이 없어 새 면접을 시작할 수 없습니다.
          관리자에게 크레딧을 요청해 주세요.
        </p>`;
    } else {
      tooltip.innerHTML = `
        <p class="text-sm font-bold text-slate-800 mb-1">⚡ 크레딧이란?</p>
        <p class="text-xs text-slate-600 leading-relaxed">
          면접을 한 번 새로 만들 때마다 <b>크레딧 1개</b> 가 사용돼요.
          남은 크레딧이 <b>0</b> 이 되면 새 면접을 시작할 수 없으니,
          그때는 관리자에게 추가 요청을 하면 됩니다.
        </p>`;
    }

    // Hover 시 표시 / 떠나면 숨김
    chip.addEventListener('mouseenter', () => tooltip.classList.remove('hidden'));
    chip.addEventListener('mouseleave', () => tooltip.classList.add('hidden'));
    // 터치 디바이스: 탭 토글
    chip.addEventListener('click', () => tooltip.classList.toggle('hidden'));

    wrap.appendChild(chip);
    wrap.appendChild(tooltip);

    // nav 맨 앞에 끼워넣기
    if (nav.firstChild) nav.insertBefore(wrap, nav.firstChild);
    else                nav.appendChild(wrap);
  });

  // ---------- 1) 모든 로그인 사용자에게 "공유 게시판" 링크 ----------
  if (location.pathname !== '/board') {
    document.querySelectorAll('nav').forEach((nav) => {
      if (nav.querySelector('a[href="/board"]')) return;
      const a = document.createElement('a');
      a.href = '/board';
      a.textContent = '공유 게시판';
      a.className = 'text-slate-600 hover:text-slate-900';
      const logoutBtn = nav.querySelector('#logoutBtn');
      // 관리 버튼이 있으면 그 앞에, 아니면 로그아웃 앞에
      const adminLink = nav.querySelector('a[href="/admin"]');
      const anchor = adminLink || logoutBtn;
      if (anchor) nav.insertBefore(a, anchor);
      else        nav.appendChild(a);
    });
  }

  // ---------- 2) admin 일 때 nav 에 "관리" 링크 추가 ----------
  if (me.role === 'admin' && location.pathname !== '/admin' && !location.pathname.startsWith('/admin/')) {
    const navs = document.querySelectorAll('nav');
    navs.forEach((nav) => {
      // 이미 있으면 skip
      if (nav.querySelector('a[href="/admin"]')) return;
      const a = document.createElement('a');
      a.href = '/admin';
      a.textContent = '관리';
      a.className = 'text-rose-600 hover:text-rose-700 font-semibold';
      // 로그아웃 버튼 앞에 끼워넣기
      const logoutBtn = nav.querySelector('#logoutBtn');
      if (logoutBtn) {
        nav.insertBefore(a, logoutBtn);
      } else {
        nav.appendChild(a);
      }
    });
  }

  // ---------- 2) impersonation 배너 ----------
  if (me.impersonating && me.admin) {
    const banner = document.createElement('div');
    banner.id = 'globalImpBanner';
    banner.className = 'bg-amber-100 border-b border-amber-300 text-amber-900 text-sm sticky top-0 z-50';
    banner.innerHTML = `
      <div class="max-w-6xl mx-auto px-6 py-2 flex items-center justify-between gap-3">
        <span>
          👁️ 현재 <b>${escapeHtml(me.name || me.email)}</b> 의 화면을 보는 중입니다.
          <span class="text-amber-700 text-xs">(관리자: ${escapeHtml(me.admin.name || me.admin.email)})</span>
        </span>
        <button id="globalExitImpBtn"
                class="px-3 py-1 rounded-md bg-amber-600 hover:bg-amber-700 text-white text-xs font-semibold whitespace-nowrap">
          관리자로 복귀
        </button>
      </div>`;
    document.body.insertBefore(banner, document.body.firstChild);

    document.getElementById('globalExitImpBtn').addEventListener('click', async () => {
      try {
        const r = await fetch('/api/admin/impersonate/exit', { method: 'POST', credentials: 'same-origin' });
        if (!r.ok) throw new Error(await r.text());
        location.href = '/admin';
      } catch (e) {
        alert('복귀 실패: ' + e.message);
      }
    });
  }

  function escapeHtml(s) {
    return String(s || '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }
})();
