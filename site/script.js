/**
 * ORBITA // DESIGN LAB - Spatial Canvas & Interaction Engine
 */

document.addEventListener('DOMContentLoaded', () => {
  initCosmicCanvas();
  initHeroParallax();
  initScopePills();
  initMobileMenu();
});

/* ==============================================================================
   COSMIC CANVAS (Dynamic Starfield & Particle Dust)
   ============================================================================== */
function initCosmicCanvas() {
  const canvas = document.getElementById('cosmic-canvas');
  if (!canvas) return;

  const ctx = canvas.getContext('2d');
  let width = (canvas.width = window.innerWidth);
  let height = (canvas.height = window.innerHeight);

  window.addEventListener('resize', () => {
    width = canvas.width = window.innerWidth;
    height = canvas.height = window.innerHeight;
    createStars();
  });

  const starCount = Math.min(Math.floor((width * height) / 8000), 160);
  let stars = [];

  function createStars() {
    stars = [];
    for (let i = 0; i < starCount; i++) {
      stars.push({
        x: Math.random() * width,
        y: Math.random() * height,
        radius: Math.random() * 1.5 + 0.3,
        alpha: Math.random() * 0.8 + 0.2,
        speedAlpha: (Math.random() * 0.02 + 0.005) * (Math.random() > 0.5 ? 1 : -1),
        vx: (Math.random() - 0.5) * 0.15,
        vy: (Math.random() - 0.5) * 0.15,
        color: Math.random() > 0.8 ? '#38bdf8' : (Math.random() > 0.6 ? '#a5b4fc' : '#ffffff')
      });
    }
  }

  createStars();

  let mouseX = width / 2;
  let mouseY = height / 2;

  window.addEventListener('mousemove', (e) => {
    mouseX = e.clientX;
    mouseY = e.clientY;
  });

  function render() {
    ctx.clearRect(0, 0, width, height);

    for (let i = 0; i < stars.length; i++) {
      const s = stars[i];

      // Twinkle
      s.alpha += s.speedAlpha;
      if (s.alpha > 0.95 || s.alpha < 0.15) {
        s.speedAlpha = -s.speedAlpha;
      }

      // Subtle motion
      s.x += s.vx;
      s.y += s.vy;

      if (s.x < 0) s.x = width;
      if (s.x > width) s.x = 0;
      if (s.y < 0) s.y = height;
      if (s.y > height) s.y = 0;

      // Draw Star
      ctx.beginPath();
      ctx.arc(s.x, s.y, s.radius, 0, Math.PI * 2);
      ctx.fillStyle = s.color;
      ctx.globalAlpha = Math.max(0, Math.min(1, s.alpha));
      ctx.fill();
    }

    requestAnimationFrame(render);
  }

  render();
}

/* ==============================================================================
   HERO 3D TILT & PARALLAX
   ============================================================================== */
function initHeroParallax() {
  const stage = document.getElementById('hero-stage');
  if (!stage) return;

  const isMobile = window.matchMedia('(max-width: 768px)').matches;
  if (isMobile) return;

  document.addEventListener('mousemove', (e) => {
    const rect = stage.getBoundingClientRect();
    const centerX = rect.left + rect.width / 2;
    const centerY = rect.top + rect.height / 2;

    const deltaX = (e.clientX - centerX) / (window.innerWidth / 2);
    const deltaY = (e.clientY - centerY) / (window.innerHeight / 2);

    const rotX = -deltaY * 6; // Max 6 deg
    const rotY = deltaX * 6;

    stage.style.transform = `perspective(1200px) rotateX(${rotX.toFixed(2)}deg) rotateY(${rotY.toFixed(2)}deg) translateZ(10px)`;
  });

  stage.addEventListener('mouseleave', () => {
    stage.style.transform = 'perspective(1200px) rotateX(0deg) rotateY(0deg) translateZ(0)';
  });
}

/* ==============================================================================
   SCOPE PILLS & INTERACTION
   ============================================================================== */
function initScopePills() {
  const pills = document.querySelectorAll('.scope-pill');
  pills.forEach((pill) => {
    pill.addEventListener('click', () => {
      pills.forEach((p) => p.classList.remove('active'));
      pill.classList.add('active');
    });
  });
}

window.handleContactSubmit = function () {
  const feedback = document.getElementById('form-feedback');
  const nameInput = document.getElementById('contact-name');
  const emailInput = document.getElementById('contact-email');

  if (!feedback) return;

  feedback.style.color = '#38bdf8';
  feedback.textContent = 'Transmission received. Orbit telemetry acknowledged.';

  setTimeout(() => {
    if (nameInput) nameInput.value = '';
    if (emailInput) emailInput.value = '';
    const msg = document.getElementById('contact-message');
    if (msg) msg.value = '';
    feedback.textContent = '';
  }, 4000);
};

/* ==============================================================================
   MOBILE MENU
   ============================================================================== */
function initMobileMenu() {
  const toggle = document.getElementById('menu-toggle');
  const nav = document.querySelector('.main-nav');
  if (!toggle || !nav) return;

  toggle.addEventListener('click', () => {
    const isHidden = getComputedStyle(nav).display === 'none';
    if (isHidden) {
      nav.style.display = 'block';
      nav.style.position = 'absolute';
      nav.style.top = '100%';
      nav.style.left = '0';
      nav.style.width = '100%';
      nav.style.background = '#0a0e1e';
      nav.style.padding = '1.5rem';
      nav.style.borderBottom = '1px solid rgba(255, 255, 255, 0.1)';
      nav.querySelector('.nav-links').style.flexDirection = 'column';
    } else {
      nav.style.display = 'none';
    }
  });
}
