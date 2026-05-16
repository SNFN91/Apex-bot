'use strict';

/* ============================================================
   RETRO VINTARA — MAIN JS
   ============================================================ */

document.addEventListener('DOMContentLoaded', () => {

  /* --- Header: scroll behavior ----------------------------- */
  const header = document.getElementById('siteHeader');
  const onScroll = () => {
    header.classList.toggle('scrolled', window.scrollY > 20);
  };
  window.addEventListener('scroll', onScroll, { passive: true });
  onScroll();

  /* --- Mobile hamburger menu ------------------------------- */
  const hamburger = document.getElementById('hamburger');
  const mainNav   = document.getElementById('mainNav');
  hamburger.addEventListener('click', () => {
    const open = hamburger.classList.toggle('open');
    hamburger.setAttribute('aria-expanded', open);
    mainNav.classList.toggle('open', open);
  });
  mainNav.querySelectorAll('.nav-link').forEach(link => {
    link.addEventListener('click', () => {
      hamburger.classList.remove('open');
      hamburger.setAttribute('aria-expanded', false);
      mainNav.classList.remove('open');
    });
  });

  /* --- Countdown timer ------------------------------------- */
  const dropDate = new Date('2026-05-22T20:00:00');
  const cdDays  = document.getElementById('cd-days');
  const cdHours = document.getElementById('cd-hours');
  const cdMins  = document.getElementById('cd-mins');
  const cdSecs  = document.getElementById('cd-secs');

  function pad(n) { return String(n).padStart(2, '0'); }

  function tick() {
    const diff = dropDate - Date.now();
    if (diff <= 0) {
      cdDays.textContent = cdHours.textContent = cdMins.textContent = cdSecs.textContent = '00';
      return;
    }
    const totalSecs = Math.floor(diff / 1000);
    const d = Math.floor(totalSecs / 86400);
    const h = Math.floor((totalSecs % 86400) / 3600);
    const m = Math.floor((totalSecs % 3600) / 60);
    const s = totalSecs % 60;

    const update = (el, val) => {
      const padded = pad(val);
      if (el.textContent !== padded) {
        el.style.animation = 'none';
        el.offsetHeight; // reflow
        el.style.animation = 'digit-flip .2s ease';
        el.textContent = padded;
      }
    };
    update(cdDays, d);
    update(cdHours, h);
    update(cdMins, m);
    update(cdSecs, s);
  }
  tick();
  setInterval(tick, 1000);

  /* --- Decade tabs ----------------------------------------- */
  const tabs  = document.querySelectorAll('.decade-tab');
  const grids = document.querySelectorAll('.vault-grid');

  tabs.forEach(tab => {
    tab.addEventListener('click', () => {
      const target = tab.dataset.decade;
      tabs.forEach(t  => t.classList.remove('active'));
      grids.forEach(g => g.classList.remove('active'));
      tab.classList.add('active');
      const grid = document.getElementById(`grid-${target}`);
      if (grid) grid.classList.add('active');
    });
  });

  /* --- Scroll reveal (IntersectionObserver) ---------------- */
  const reveals = document.querySelectorAll('.reveal');
  if ('IntersectionObserver' in window) {
    const io = new IntersectionObserver((entries) => {
      entries.forEach(entry => {
        if (entry.isIntersecting) {
          entry.target.classList.add('in-view');
          io.unobserve(entry.target);
        }
      });
    }, { threshold: 0.12 });
    reveals.forEach(el => io.observe(el));
  } else {
    reveals.forEach(el => el.classList.add('in-view'));
  }

  /* --- Add-to-cart simulation ------------------------------ */
  let cartCount = 0;
  const cartCountEl = document.querySelector('.cart-count');
  const cartBtn     = document.querySelector('.cart-btn');

  document.querySelectorAll('.btn-primary').forEach(btn => {
    if (!btn.closest('.club-form') && !btn.closest('.header-actions')) {
      btn.addEventListener('click', (e) => {
        e.preventDefault();
        cartCount++;
        cartCountEl.textContent = cartCount;
        cartBtn.classList.remove('bounce');
        cartBtn.offsetHeight;
        cartBtn.classList.add('bounce');
        cartBtn.addEventListener('animationend', () => cartBtn.classList.remove('bounce'), { once: true });
        const orig = btn.textContent;
        btn.textContent = '✓ ADDED TO SIGNAL';
        btn.style.background = '#00FF41';
        setTimeout(() => {
          btn.textContent = orig;
          btn.style.background = '';
        }, 1600);
      });
    }
  });

  /* --- Email capture form ---------------------------------- */
  const clubForm = document.getElementById('clubForm');
  if (clubForm) {
    clubForm.addEventListener('submit', (e) => {
      e.preventDefault();
      const input   = clubForm.querySelector('.club-input');
      const success = document.getElementById('formSuccess');
      if (!input.value.trim()) return;
      clubForm.style.display = 'none';
      if (success) success.style.display = 'block';
    });
  }

  /* --- Audio toggle (UI only — wire to your audio source) -- */
  const audioToggle = document.getElementById('audioToggle');
  let audioPlaying = false;
  /* Uncomment and set src to enable real audio:
     const audio = new Audio('audio/ambient.mp3');
     audio.loop = true; audio.volume = .18; */
  if (audioToggle) {
    audioToggle.addEventListener('click', () => {
      audioPlaying = !audioPlaying;
      audioToggle.classList.toggle('active', audioPlaying);
      audioToggle.querySelector('.audio-label').textContent = audioPlaying ? 'MUTE' : 'AMBIENT';
      /* audio[audioPlaying ? 'play' : 'pause'](); */
    });
  }

  /* --- Product card quick-view (prevent nav) --------------- */
  document.querySelectorAll('.quick-view').forEach(link => {
    link.addEventListener('click', e => {
      e.preventDefault();
      /* wire to your modal / product page */
    });
  });

});
