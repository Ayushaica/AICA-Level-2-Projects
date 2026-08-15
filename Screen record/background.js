chrome.runtime.onInstalled.addListener(() => {
  chrome.storage.local.set({ isRecording: false, steps: [] });
});

chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === "getState") {
    chrome.storage.local.get(['isRecording', 'steps'], result => {
      sendResponse(result);
    });
    return true; // async
  }
  
  if (request.action === "setRecording") {
    chrome.storage.local.set({ isRecording: request.isRecording }, () => {
      sendResponse({ success: true });
    });
    return true;
  }
  
  if (request.action === "clearSteps") {
    chrome.storage.local.set({ steps: [] }, () => {
      sendResponse({ success: true });
    });
    return true;
  }
  
  if (request.action === "captureStep") {
    // We add a tiny delay to ensure the DOM has painted the yellow dot.
    setTimeout(() => {
      chrome.tabs.captureVisibleTab(null, { format: 'jpeg', quality: 60 }, (dataUrl) => {
        if (chrome.runtime.lastError) {
          console.error("Capture Error:", chrome.runtime.lastError);
          sendResponse({ success: false, error: chrome.runtime.lastError.message });
          return;
        }
        
        chrome.storage.local.get('steps', result => {
          const steps = result.steps || [];
          steps.push({
             imageUri: dataUrl,
             url: request.url,
             title: request.title || 'Step ' + (steps.length + 1),
             timestamp: new Date().toISOString()
          });
          chrome.storage.local.set({ steps }, () => {
             // Return success to content script so it can remove the marker
             sendResponse({ success: true });
          });
        });
      });
    }, 100); // 100ms delay to let the UI update
    return true; // Keep message channel open for async response
  }
});
