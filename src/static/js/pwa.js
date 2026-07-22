(() => {
  const script = document.currentScript;
  const workerUrl = script?.dataset.serviceWorkerUrl;
  const workerScope = script?.dataset.serviceWorkerScope;
  let installPrompt = null;
  let reloadForUpdate = false;
  let refreshing = false;

  const isInstalled = () => (
    window.matchMedia('(display-mode: standalone)').matches
    || window.navigator.standalone === true
  );

  const updateInstallControls = () => {
    const available = Boolean(installPrompt) && !isInstalled();
    document.querySelectorAll('[data-pwa-install]').forEach((button) => {
      button.disabled = !available;
    });
    const section = document.getElementById('pwa-install-section');
    if (section) {
      section.classList.toggle('hidden', !available);
    }
  };

  window.addEventListener('beforeinstallprompt', (event) => {
    event.preventDefault();
    installPrompt = event;
    updateInstallControls();
  });

  window.addEventListener('appinstalled', () => {
    installPrompt = null;
    updateInstallControls();
  });

  document.addEventListener('click', async (event) => {
    const button = event.target.closest('[data-pwa-install]');
    if (!button || !installPrompt) {
      return;
    }
    button.disabled = true;
    await installPrompt.prompt();
    await installPrompt.userChoice;
    installPrompt = null;
    updateInstallControls();
  });

  const showUpdateNotice = (registration) => {
    const notice = document.getElementById('pwa-update-notice');
    const button = document.getElementById('pwa-update-button');
    if (!notice || !button || !registration.waiting) {
      return;
    }
    notice.classList.remove('hidden');
    button.addEventListener('click', () => {
      reloadForUpdate = true;
      registration.waiting?.postMessage({ type: 'SKIP_WAITING' });
    }, { once: true });
  };

  if ('serviceWorker' in navigator && workerUrl && workerScope) {
    window.addEventListener('load', async () => {
      try {
        const registrations = await navigator.serviceWorker.getRegistrations();
        await Promise.all(registrations.map((existingRegistration) => {
          const scriptUrl = existingRegistration.active?.scriptURL || '';
          if (scriptUrl.endsWith('/static/js/serviceworker.js')) {
            return existingRegistration.unregister();
          }
          return undefined;
        }));
        const registration = await navigator.serviceWorker.register(workerUrl, {
          scope: workerScope,
          updateViaCache: 'none',
        });
        if (registration.waiting && navigator.serviceWorker.controller) {
          showUpdateNotice(registration);
        }
        registration.addEventListener('updatefound', () => {
          const worker = registration.installing;
          worker?.addEventListener('statechange', () => {
            if (worker.state === 'installed' && navigator.serviceWorker.controller) {
              showUpdateNotice(registration);
            }
          });
        });
      } catch (error) {
        console.warn('MyTrackz service worker registration failed.', error);
      }
    });

    navigator.serviceWorker.addEventListener('controllerchange', () => {
      if (reloadForUpdate && !refreshing) {
        refreshing = true;
        window.location.reload();
      }
    });
  }

  updateInstallControls();
})();
