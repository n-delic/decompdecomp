"use strict";
// The platform picker (one pipeline at a time) and the copy button. Without JavaScript every pipeline shows.
(() => {
  document.documentElement.classList.add("js");
  document.querySelectorAll(".js-only").forEach((el) => { el.hidden = false; });

  const answer = document.getElementById("answer");
  const show = (value) => {
    document.querySelectorAll(".pipeline").forEach((p) => {
      const on = p.dataset.platform === value;
      p.classList.toggle("on", on);
      if (on) {
        const built = p.querySelectorAll(".stations b")[2].textContent;
        answer.textContent = `${p.querySelector(".pl-name").textContent}: ${p.querySelector(".status").textContent}. Built by ${built}.`;
      }
    });
  };
  const picker = document.querySelector(".platforms");
  picker.addEventListener("change", (e) => show(e.target.value));
  show(picker.querySelector("input:checked").value);

  // the clipboard can be missing or refused (older browsers, privacy settings): then the text is selected for Ctrl+C
  const said = (b, text) => { b.textContent = text; setTimeout(() => { b.textContent = "Copy"; }, 1600); };
  document.querySelectorAll("[data-copy-from]").forEach((b) => b.addEventListener("click", () => {
    const code = document.getElementById(b.dataset.copyFrom);
    const select = () => {
      const r = document.createRange();
      r.selectNodeContents(code);
      getSelection().removeAllRanges();
      getSelection().addRange(r);
      said(b, "Press Ctrl+C");
    };
    if (!navigator.clipboard || !window.isSecureContext) return select();
    navigator.clipboard.writeText(code.textContent).then(() => said(b, "Copied"), select);
  }));
})();
