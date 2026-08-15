document.addEventListener('DOMContentLoaded', () => {
  const startBtn = document.getElementById('startBtn');
  const stopBtn = document.getElementById('stopBtn');
  const userNameInput = document.getElementById('userName');
  const statusCard = document.getElementById('statusCard');
  const statusText = document.getElementById('statusText');
  const stepCountBadge = document.getElementById('stepCount');

  // Load state and steps
  chrome.runtime.sendMessage({ action: "getState" }, (res) => {
    updateUI(res?.isRecording || false, res?.steps || []);
    // Try to load any saved username
    chrome.storage.local.get('userName', (data) => {
      if (data.userName) userNameInput.value = data.userName;
    });
  });

  // Listen for storage changes in case background captures a step while popup is open
  chrome.storage.onChanged.addListener((changes) => {
    if (changes.steps) {
      updateStepCount(changes.steps.newValue.length);
    }
    if (changes.isRecording) {
      chrome.runtime.sendMessage({ action: "getState" }, (res) => {
         updateUI(res.isRecording, res.steps || []);
      });
    }
  });

  function updateUI(isRecording, steps) {
    if (isRecording) {
      startBtn.style.display = 'none';
      stopBtn.style.display = 'flex';
      statusCard.classList.add('recording');
      statusText.innerText = 'Recording active...';
    } else {
      startBtn.style.display = 'flex';
      stopBtn.style.display = 'none';
      statusCard.classList.remove('recording');
      statusText.innerText = 'Ready to record';
    }
    if (steps) {
      updateStepCount(steps.length);
    }
  }

  function updateStepCount(count) {
    if (count > 0) {
      stepCountBadge.style.display = 'block';
      stepCountBadge.innerText = count === 1 ? '1 step' : `${count} steps`;
    } else {
      stepCountBadge.style.display = 'none';
    }
  }

  startBtn.addEventListener('click', () => {
    const name = userNameInput.value.trim() || 'Anonymous';
    chrome.storage.local.set({ userName: name });

    chrome.runtime.sendMessage({ action: "clearSteps" }, () => {
      chrome.runtime.sendMessage({ action: "setRecording", isRecording: true }, () => {
        updateUI(true, []);
      });
    });
  });

  stopBtn.addEventListener('click', () => {
    const name = userNameInput.value.trim() || 'Anonymous';
    chrome.storage.local.set({ userName: name });

    chrome.runtime.sendMessage({ action: "setRecording", isRecording: false }, () => {
      chrome.runtime.sendMessage({ action: "getState" }, (res) => {
        updateUI(false, res.steps || []);
        generatePDF(res.steps || [], name);
      });
    });
  });

  async function generatePDF(steps, authorName) {
    if (steps.length === 0) {
      alert("No steps were recorded.");
      return;
    }

    statusText.innerText = 'Generating PDF...';
    
    // Deconstruct jsPDF
    const { jsPDF } = window.jspdf;
    const doc = new jsPDF();
    const pageWidth = doc.internal.pageSize.getWidth();
    const pageHeight = doc.internal.pageSize.getHeight();
    const margin = 10;
    
    doc.setFont("helvetica", "bold");
    doc.setFontSize(22);
    doc.setTextColor(40, 40, 40);
    doc.text("Standard Operating Procedure", margin, 20);
    
    doc.setFontSize(12);
    doc.setFont("helvetica", "normal");
    doc.setTextColor(100, 100, 100);
    doc.text(`Document created by: ${authorName}`, margin, 30);
    doc.text(`Date: ${new Date().toLocaleDateString()}`, margin, 38);
    
    doc.setDrawColor(200, 200, 200);
    doc.line(margin, 42, pageWidth - margin, 42);

    let yOffset = 50;

    for (let i = 0; i < steps.length; i++) {
        const step = steps[i];
        
        // 1 screenshot per page (except first page where it follows the title)
        if (i > 0) {
            doc.addPage();
            yOffset = 20;
        } else {
            yOffset = 50;
        }

        // Add Step Title
        doc.setFontSize(14);
        doc.setFont("helvetica", "bold");
        doc.setTextColor(40, 40, 40);
        doc.text(`Step ${i + 1}`, margin, yOffset);
        
        // Add Step URL
        yOffset += 8;
        doc.setFontSize(10);
        doc.setFont("helvetica", "normal");
        doc.setTextColor(59, 130, 246); // Blue link color
        // Truncate URL if too long
        const urlStr = step.url.length > 80 ? step.url.substring(0, 80) + '...' : step.url;
        doc.text(urlStr, margin, yOffset);
        
        yOffset += 5;

        // Determine image dimensions to fit within the page
        try {
          const imgProps = doc.getImageProperties(step.imageUri);
          const maxImgWidth = pageWidth - (margin * 2);
          const maxImgHeight = pageHeight - yOffset - margin;

          const imgWidthScale = maxImgWidth / imgProps.width;
          const imgHeightScale = maxImgHeight / imgProps.height;
          const scale = Math.min(imgWidthScale, imgHeightScale);

          const drawnWidth = imgProps.width * scale;
          const drawnHeight = imgProps.height * scale;

          // Draw border around image
          doc.setDrawColor(220, 220, 220);
          doc.rect(margin - 0.5, yOffset - 0.5, drawnWidth + 1, drawnHeight + 1);
          
          doc.addImage(step.imageUri, 'JPEG', margin, yOffset, drawnWidth, drawnHeight);
        } catch(e) {
          console.error("Failed to add image to PDF", e);
          doc.text("(Image capture failed)", margin, yOffset + 10);
        }
    }

    doc.save(`SOP_${authorName.replace(/\s+/g, '_')}_${Date.now()}.pdf`);
    statusText.innerText = 'PDF Downloaded!';
    
    // Clear out the steps from storage now that we're done
    setTimeout(() => {
      chrome.runtime.sendMessage({ action: "clearSteps" });
      updateStepCount(0);
    }, 2000);
  }
});
