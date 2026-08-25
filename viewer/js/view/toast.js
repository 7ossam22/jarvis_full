// js/view/toast.js — the caption box showing JARVIS's last answer (View
// layer). Stays up exactly as long as he's speaking: hideAnswer() is called
// only from controller/speechController.js once playback actually finishes,
// never on a fixed timer.
const answerToast = document.getElementById("answer-toast");

export function showAnswer(text) {
  if (!text) return;
  answerToast.textContent = text;
  answerToast.classList.add("show");
}

export function hideAnswer() {
  answerToast.classList.remove("show");
}
