/* The browser and iOS ports share the same controller. Track fingers separately so holding a direction and two
   buttons works, and releasing one finger never releases another. Pointer capture also catches cancelled drags. */
export function touchController(root) {
  const buttons = new Map();
  let stickPointer = null, x = 0, y = 0;
  const stick = root.querySelector("[data-stick]");
  const dot = stick.querySelector("span");
  function resetStick() {
    stickPointer = null; x = y = 0;
    dot.style.transform = "translate(0px, 0px)";
  }
  function clear() { buttons.clear(); resetStick(); }
  function move(event) {
    if (event.pointerId !== stickPointer) return;
    const rect = stick.getBoundingClientRect(), radius = Math.max(1, rect.width / 2 - 20);
    let dx = (event.clientX - rect.left - rect.width / 2) / radius;
    let dy = (event.clientY - rect.top - rect.height / 2) / radius;
    const length = Math.hypot(dx, dy);
    if (length > 1) { dx /= length; dy /= length; }
    x = Math.round(dx * 80); y = Math.round(-dy * 80);
    dot.style.transform = `translate(${dx * radius}px, ${dy * radius}px)`;
  }
  stick.addEventListener("pointerdown", (event) => {
    event.preventDefault();
    if (stickPointer !== null) return;
    stickPointer = event.pointerId;
    stick.setPointerCapture(event.pointerId);
    move(event);
  });
  stick.addEventListener("pointermove", move);
  for (const type of ["pointerup", "pointercancel", "lostpointercapture"]) {
    stick.addEventListener(type, (event) => { if (event.pointerId === stickPointer) resetStick(); });
  }
  for (const button of root.querySelectorAll("[data-button]")) {
    button.addEventListener("pointerdown", (event) => {
      event.preventDefault();
      buttons.set(event.pointerId, Number(button.dataset.button));
      button.setPointerCapture(event.pointerId);
    });
    for (const type of ["pointerup", "pointercancel", "lostpointercapture"]) {
      button.addEventListener(type, (event) => buttons.delete(event.pointerId));
    }
  }
  addEventListener("blur", clear);
  addEventListener("resize", clear);
  document.addEventListener("visibilitychange", () => { if (document.hidden) clear(); });
  return { read() {
    let mask = 0;
    for (const value of buttons.values()) mask |= value;
    return { buttons: mask, x, y };
  } };
}
