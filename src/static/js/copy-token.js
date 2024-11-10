document.addEventListener('DOMContentLoaded', function() {
  // Initialize tooltips
  var tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
  var tooltipList = tooltipTriggerList.map(function(tooltipTriggerEl) {
      return new bootstrap.Tooltip(tooltipTriggerEl);
  });

  // Get elements
  const tokenInput = document.getElementById('token-input');
  const copyButton = document.getElementById('copyButton');

  // Select all text when clicking the input
  tokenInput.addEventListener('click', function() {
      this.select();
  });

  // Copy button functionality
  copyButton.addEventListener('click', function() {
      tokenInput.select();
      
      // Try to copy using document.execCommand (older browsers)
      try {
          const successful = document.execCommand('copy');
          if (successful) {
              showCopiedTooltip();
          } else {
              fallbackCopy();
          }
      } catch (err) {
          fallbackCopy();
      }
  });

  // Fallback copy method using clipboard API
  function fallbackCopy() {
      const text = tokenInput.value;
      if (window.isSecureContext && navigator.clipboard) {
          navigator.clipboard.writeText(text).then(() => {
              showCopiedTooltip();
          }).catch(() => {
              alert('Failed to copy to clipboard. Please press Ctrl+C to copy.');
          });
      } else {
          // Fallback for non-secure context
          alert('Please press Ctrl+C to copy the token.');
      }
  }

  // Show copied tooltip feedback
  function showCopiedTooltip() {
      const originalTitle = copyButton.getAttribute('data-bs-original-title');
      copyButton.setAttribute('data-bs-original-title', 'Copied!');
      bootstrap.Tooltip.getInstance(copyButton).show();
      
      setTimeout(() => {
          copyButton.setAttribute('data-bs-original-title', originalTitle);
      }, 1000);
  }
});