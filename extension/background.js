// Settings contain a backend token; content scripts must not read extension storage.
chrome.storage.local.setAccessLevel({accessLevel: 'TRUSTED_CONTEXTS'});
chrome.runtime.onInstalled.addListener(() => {
  chrome.storage.local.setAccessLevel({accessLevel: 'TRUSTED_CONTEXTS'});
});
