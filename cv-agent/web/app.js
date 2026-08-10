const $ = (id) => document.getElementById(id);
let lastPage = null;

function setStatus(message, isError = false) {
  const status = $("status");
  status.textContent = message;
  status.classList.toggle("error", isError);
}

function renderAnalysis(data) {
  const cv = data.cv;
  $("analysis").classList.remove("hidden");
  $("read").textContent = `Read as: ${cv.category_read}. Roles kept: ${cv.selected_roles.join(", ")}.`;
  $("reasoning").textContent = cv.selection_reasoning;

  const keywords = $("keywords");
  keywords.replaceChildren();
  for (const word of cv.listing_keywords || []) {
    const chip = document.createElement("span");
    chip.textContent = word;
    keywords.append(chip);
  }

  const check = data.verification;
  const box = $("verification");
  box.replaceChildren();
  box.className = `verify ${check.passed ? "pass" : "fail"}`;
  const headline = document.createElement("div");
  if (check.passed) {
    headline.textContent =
      `Rewrite verified: longest run reused from the source is ${check.max_source_run} ` +
      `word(s), limit ${check.limit}. ${data.rounds > 1 ? `Took ${data.rounds} rounds.` : "Clean first pass."}`;
  } else {
    headline.textContent =
      `${check.violations.length} bullet(s) still too close to the source after ${data.rounds} rounds:`;
  }
  box.append(headline);
  if (!check.passed) {
    const list = document.createElement("ul");
    for (const violation of check.violations) {
      const item = document.createElement("li");
      item.textContent = `${violation.role}: ${violation.detail}`;
      list.append(item);
    }
    box.append(list);
  }
}

function renderPreview(data) {
  let style = document.getElementById("cv-css");
  if (!style) {
    style = document.createElement("style");
    style.id = "cv-css";
    document.head.append(style);
  }
  style.textContent = data.css;
  $("cv").innerHTML = data.html;
  lastPage = data.page;
  $("print").disabled = false;
  $("preview-label").textContent = `Preview — ${data.cv.category_read}`;
}

async function generate() {
  const listing = $("listing").value.trim();
  if (!listing) {
    setStatus("Paste the job listing first.", true);
    return;
  }
  $("generate").disabled = true;
  setStatus("Tailoring — extracting facts, rewriting, then verifying the rewrite…");
  try {
    const response = await fetch("/api/tailor", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ listing, hint: $("hint").value }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || response.statusText);
    renderAnalysis(data);
    renderPreview(data);
    setStatus("Done.");
  } catch (error) {
    setStatus(error.message, true);
  } finally {
    $("generate").disabled = false;
  }
}

$("generate").addEventListener("click", generate);
$("print").addEventListener("click", () => {
  if (!lastPage) return;
  const win = window.open("", "_blank");
  win.document.write(lastPage);
  win.document.close();
  win.focus();
  win.print();
});
