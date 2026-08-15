let isRecording = false;

// Initialize state
chrome.storage.local.get('isRecording', (result) => {
  isRecording = result.isRecording || false;
});

// Listen for state changes
chrome.storage.onChanged.addListener((changes, namespace) => {
  if (changes.isRecording) {
    isRecording = changes.isRecording.newValue;
  }
});

document.addEventListener('mousedown', (e) => {
  if (!isRecording) return;
  // Proceed only for primary left click
  if (e.button !== 0) return;

  // Create the yellow marker
  const marker = document.createElement('div');
  marker.style.position = 'absolute';
  marker.style.left = (e.pageX - 20) + 'px';
  marker.style.top = (e.pageY - 20) + 'px';
  marker.style.width = '40px';
  marker.style.height = '40px';
  marker.style.borderRadius = '50%';
  marker.style.backgroundColor = 'rgba(255, 255, 0, 0.4)';
  marker.style.border = '3px solid #ffaa00';
  marker.style.pointerEvents = 'none'; // Don't block subsequent clicks/events
  marker.style.zIndex = '2147483647'; // Max z-index
  marker.style.boxShadow = '0 0 10px rgba(0,0,0,0.5)';
  marker.style.transition = 'transform 0.1s ease-out';
  
  document.body.appendChild(marker);

  // A tiny animation to make it look premium
  requestAnimationFrame(() => {
    marker.style.transform = 'scale(0.8)';
  });

  // Ask background script to take screenshot
  chrome.runtime.sendMessage({
    action: 'captureStep',
    url: window.location.href,
    title: document.title
  }, (response) => {
    // Clean up marker
    if (marker && marker.parentNode) {
      marker.parentNode.removeChild(marker);
    }
  });

}, true); // Use capture phase so we trigger before other elements handle the click and potentially navigate
