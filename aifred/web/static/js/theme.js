// Runs before the first paint so the chosen theme does not flash.
try {
  const saved = localStorage.getItem("aifred.theme");
  if (saved === "teal" || saved === "sand") document.documentElement.dataset.theme = saved;
} catch (e) { /* storage can be blocked */ }
