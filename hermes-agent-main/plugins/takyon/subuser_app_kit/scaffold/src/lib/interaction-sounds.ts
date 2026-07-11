let installed = false;

function playButtonSound() {
  const AudioContextClass = window.AudioContext;
  if (!AudioContextClass) return;
  const context = new AudioContextClass();
  const oscillator = context.createOscillator();
  const gain = context.createGain();
  oscillator.type = "sine";
  oscillator.frequency.setValueAtTime(520, context.currentTime);
  oscillator.frequency.exponentialRampToValueAtTime(680, context.currentTime + 0.045);
  gain.gain.setValueAtTime(0.025, context.currentTime);
  gain.gain.exponentialRampToValueAtTime(0.0001, context.currentTime + 0.06);
  oscillator.connect(gain);
  gain.connect(context.destination);
  oscillator.start();
  oscillator.stop(context.currentTime + 0.06);
  oscillator.addEventListener("ended", () => void context.close(), { once: true });
}

export function installInteractionSounds() {
  if (installed) return;
  installed = true;
  document.addEventListener("click", (event) => {
    const target = event.target instanceof Element ? event.target.closest("button") : null;
    if (!(target instanceof HTMLButtonElement) || target.disabled) return;
    try {
      playButtonSound();
    } catch {
      // Audio is progressive enhancement; browser policy must never block the interaction.
    }
  });
}
