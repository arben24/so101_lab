// Schaltflächen „In der Web-App ausprobieren“: nur aktiv, wenn die Doku über `so101-lab web`
// ausgeliefert wird (dann antwortet /api/info). Bei `mkdocs serve` oder GitLab Pages bleiben
// sie sichtbar, aber deaktiviert, mit einem Hinweis.
(function () {
  "use strict";
  function disable(links) {
    for (const a of links) {
      a.classList.add("lab-disabled");
      a.removeAttribute("href");
      a.title = "Nur verfügbar, wenn die Doku über „uv run so101-lab web“ geöffnet ist.";
      if (!a.querySelector(".lab-note")) {
        const note = document.createElement("span");
        note.className = "lab-note";
        note.textContent = " (nur mit laufender Web-App)";
        a.append(note);
      }
    }
  }
  function check() {
    const links = document.querySelectorAll("a.lab-link");
    if (!links.length) return;
    fetch("/api/info", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((info) => {
        if (!("auth_required" in info)) disable(links);
      })
      .catch(() => disable(links));
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", check);
  else check();
})();
