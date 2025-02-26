document.addEventListener("DOMContentLoaded", function () {
  // Get elements - using more reliable selectors
  const tokenInput = document.querySelector("input[readonly]");

  // Find the copy button by looking for buttons and checking their text content
  let copyButton = null;
  const buttons = document.querySelectorAll("button");
  buttons.forEach((button) => {
    if (button.textContent.trim().includes("Copy")) {
      copyButton = button;
    }
  });

  if (!tokenInput || !copyButton) {
    console.error("Could not find token input or copy button");
    return;
  }

  // Copy button functionality
  copyButton.addEventListener("click", function () {
    const text = tokenInput.value;

    // Try to copy the text
    tokenInput.select();
    tokenInput.setSelectionRange(0, 99999); // For mobile devices

    let copied = false;

    // Try clipboard API first
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard
        .writeText(text)
        .then(() => {
          showSuccess();
        })
        .catch((err) => {
          console.error("Clipboard API failed:", err);
          tryExecCommand();
        });
    } else {
      tryExecCommand();
    }

    function tryExecCommand() {
      try {
        copied = document.execCommand("copy");
        if (copied) {
          showSuccess();
        } else {
          showError();
        }
      } catch (err) {
        console.error("execCommand failed:", err);
        showError();
      }
    }

    function showSuccess() {
      const textNode = Array.from(copyButton.childNodes).find(
        (node) =>
          node.nodeType === Node.TEXT_NODE && node.textContent.trim() === "Copy"
      );

      if (textNode) {
        textNode.textContent = " Copied!";
      }

      // Change background color
      copyButton.style.backgroundColor = "#059669"; // green-600
    }

    function showError() {
      alert(
        "Could not copy to clipboard. Please select the text and press Ctrl+C/Cmd+C to copy."
      );
    }
  });

  // Make token input select all text when clicked
  tokenInput.addEventListener("click", function () {
    this.select();
  });
});
