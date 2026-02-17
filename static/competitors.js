(() => {
  const form = document.getElementById("competitors-form");
  const submitBtn = document.getElementById("competitors-submit");

  const progressWrap = document.getElementById("comp-progress-wrap");
  const progressIdle = document.getElementById("comp-progress-idle");
  const progressBar = document.getElementById("comp-progress-bar");
  const progressPercent = document.getElementById("comp-progress-percent");
  const progressStatus = document.getElementById("comp-progress-status");
  const progressEta = document.getElementById("comp-progress-eta");

  const emptyState = document.getElementById("comp-empty-state");
  const resultView = document.getElementById("comp-result-view");
  const runTitle = document.getElementById("comp-run-title");
  const errorsBox = document.getElementById("comp-errors");
  const analysisPre = document.getElementById("comp-analysis");
  const pagesGrid = document.getElementById("comp-pages-grid");

  let progressTimer = null;
  let progress = 0;
  let startTs = 0;

  const formatEta = (seconds) => {
    const safe = Math.max(0, Math.round(seconds));
    const mm = String(Math.floor(safe / 60)).padStart(2, "0");
    const ss = String(safe % 60).padStart(2, "0");
    return `~${mm}:${ss}`;
  };

  const updateProgress = () => {
    const elapsed = (Date.now() - startTs) / 1000;
    const remaining = progress > 2 ? elapsed * ((100 - progress) / progress) : 140;
    let status = "Этап 1/3: переход по сайтам";
    if (progress >= 35) status = "Этап 2/3: скриншоты и текст";
    if (progress >= 80) status = "Этап 3/3: финальный анализ";

    progressBar.style.width = `${progress}%`;
    progressPercent.textContent = `${Math.round(progress)}%`;
    progressStatus.textContent = `${status}…`;
    progressEta.textContent = `Примерное время до окончания: ${formatEta(remaining)}`;
  };

  const startProgress = () => {
    progress = 3;
    startTs = Date.now();
    progressIdle.classList.add("hidden");
    progressWrap.classList.remove("hidden");
    updateProgress();

    progressTimer = window.setInterval(() => {
      if (progress < 92) progress += 1.1;
      updateProgress();
    }, 1000);
  };

  const completeProgress = () => {
    if (progressTimer) {
      clearInterval(progressTimer);
      progressTimer = null;
    }
    progress = 100;
    progressBar.style.width = "100%";
    progressPercent.textContent = "100%";
    progressStatus.textContent = "Анализ завершён";
    progressEta.textContent = "Готово";
    window.setTimeout(() => {
      progressWrap.classList.add("hidden");
      progressIdle.classList.remove("hidden");
      progressIdle.textContent = "Анализ конкурентов завершён.";
    }, 700);
  };

  const failProgress = () => {
    if (progressTimer) {
      clearInterval(progressTimer);
      progressTimer = null;
    }
    progressStatus.textContent = "Ошибка анализа";
    progressEta.textContent = "Проверьте сообщения об ошибке";
    window.setTimeout(() => {
      progressWrap.classList.add("hidden");
      progressIdle.classList.remove("hidden");
      progressIdle.textContent = "Анализ завершился с ошибкой.";
    }, 700);
  };

  const createPageCard = (page) => {
    const article = document.createElement("article");
    article.className = "page-card";

    const title = document.createElement("h4");
    const link = document.createElement("a");
    link.href = page.url;
    link.target = "_blank";
    link.rel = "noreferrer";
    link.textContent = page.url;
    title.appendChild(link);

    const pageTitle = document.createElement("p");
    pageTitle.innerHTML = `<b>Title:</b> ${page.title || "-"}`;

    const details = document.createElement("details");
    const summary = document.createElement("summary");
    summary.textContent = "Текст страницы";
    const txt = document.createElement("p");
    txt.textContent = page.text_excerpt || "Нет текста";
    details.appendChild(summary);
    details.appendChild(txt);

    const shots = document.createElement("div");
    shots.className = "shots";

    const desktop = document.createElement("a");
    desktop.href = `/${page.desktop_screenshot}`;
    desktop.target = "_blank";
    desktop.textContent = "Desktop screenshot";

    const mobile = document.createElement("a");
    mobile.href = `/${page.mobile_screenshot}`;
    mobile.target = "_blank";
    mobile.textContent = "Mobile screenshot";

    shots.appendChild(desktop);
    shots.appendChild(mobile);

    article.appendChild(title);
    article.appendChild(pageTitle);
    article.appendChild(details);
    article.appendChild(shots);

    return article;
  };

  const renderResult = (payload) => {
    emptyState.classList.add("hidden");
    resultView.classList.remove("hidden");
    runTitle.textContent = `Отчёт по конкурентам ${payload.run_id || ""}`.trim();

    analysisPre.textContent = payload.analysis || "";
    pagesGrid.replaceChildren();
    (payload.pages || []).forEach((p) => pagesGrid.appendChild(createPageCard(p)));

    if (payload.errors && payload.errors.length > 0) {
      errorsBox.classList.remove("hidden");
      errorsBox.replaceChildren();
      payload.errors.forEach((err) => {
        const p = document.createElement("p");
        p.textContent = err;
        errorsBox.appendChild(p);
      });
    } else {
      errorsBox.classList.add("hidden");
      errorsBox.replaceChildren();
    }
  };

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    submitBtn.disabled = true;
    startProgress();

    try {
      const formData = new FormData(form);
      const response = await fetch("/analyze-competitors", {
        method: "POST",
        body: formData,
      });
      const payload = await response.json();
      renderResult(payload);

      if (!response.ok || (payload.errors && payload.errors.length > 0 && !payload.analysis)) {
        failProgress();
      } else {
        completeProgress();
      }
    } catch (error) {
      renderResult({ run_id: "", pages: [], analysis: "", errors: [String(error)] });
      failProgress();
    } finally {
      submitBtn.disabled = false;
    }
  });
})();
