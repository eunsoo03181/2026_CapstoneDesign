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
