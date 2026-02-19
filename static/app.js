(() => {
  const form = document.getElementById("analyze-form");
  const submitBtn = document.getElementById("submit-btn");

  const progressWrap = document.getElementById("progress-wrap");
  const progressIdle = document.getElementById("progress-idle");
  const progressBar = document.getElementById("progress-bar");
  const progressPercent = document.getElementById("progress-percent");
  const progressStatus = document.getElementById("progress-status");
  const progressEta = document.getElementById("progress-eta");
  const progressSteps = document.getElementById("progress-steps");

  const emptyState = document.getElementById("empty-state");
  const resultView = document.getElementById("result-view");
  const runIdTitle = document.getElementById("run-id-title");
  const reportMeta = document.getElementById("report-meta");
  const errorsBox = document.getElementById("errors-box");

  const copyReportBtn = document.getElementById("copy-report-btn");
  const downloadPdfBtn = document.getElementById("download-pdf-btn");
  const exportSheetsBtn = document.getElementById("export-sheets-btn");

  const finalSummary = document.getElementById("final-summary");
  const summarySection = document.getElementById("summary-section");
  const metricsAnalysis = document.getElementById("metrics-analysis");
  const metricsSection = document.getElementById("metrics-section");
  const audienceAnalysis = document.getElementById("audience-analysis");
  const pagesAnalysis = document.getElementById("pages-analysis");
  const pagesGrid = document.getElementById("pages-grid");

  const headerMoreToggle = document.getElementById("header-more-toggle");
  const headerMore = document.getElementById("header-more");

  const auditMode = document.getElementById("audit-mode");
  const fullOnlyBlocks = document.querySelectorAll(".full-only");
  const screenshotOnlyBlocks = document.querySelectorAll(".screenshot-only");

  const filesInput = document.getElementById("files-input");
  const filePickerBtn = document.getElementById("file-picker-btn");
  const filesCount = document.getElementById("files-count");
  const filesChips = document.getElementById("files-chips");
  const filesHelpToggle = document.getElementById("files-help-toggle");
  const filesHelp = document.getElementById("files-help");

  const pageUrlInput = document.getElementById("page-url");

  const topNMode = document.getElementById("top-n-mode");
  const customTopN = document.getElementById("custom-top-n");
  const topNValue = document.getElementById("top-n-value");

  const rolePromptField = document.getElementById("role-prompt");
  const metricsPromptField = document.getElementById("metrics-prompt");
  const audiencePromptField = document.getElementById("audience-prompt");
  const pagesPromptField = document.getElementById("pages-prompt");
  const promptToggles = document.querySelectorAll(".prompt-toggle");

  let progressTimer = null;
  let progress = 0;
  let startTs = 0;
  let lastResult = null;

  const fullStageTimeline = [
    { limit: 20, text: "Этап 1/5: загрузка файлов", step: 1 },
    { limit: 35, text: "Этап 2/5: выбор страниц", step: 2 },
    { limit: 60, text: "Этап 3/5: скриншоты", step: 3 },
    { limit: 82, text: "Этап 4/5: извлечение текста", step: 4 },
    { limit: 98, text: "Этап 5/5: рекомендации", step: 5 },
  ];

  const screenshotStageTimeline = [
    { limit: 22, text: "Этап 1/5: переход по ссылке", step: 2 },
    { limit: 56, text: "Этап 2/5: скриншоты", step: 3 },
    { limit: 78, text: "Этап 3/5: извлечение текста", step: 4 },
    { limit: 98, text: "Этап 4/5: рекомендации", step: 5 },
  ];

  const getCurrentTimeline = () => {
    return auditMode && auditMode.value === "screenshot" ? screenshotStageTimeline : fullStageTimeline;
  };

  const formatEta = (seconds) => {
    const safe = Math.max(0, Math.round(seconds));
    const mm = String(Math.floor(safe / 60)).padStart(2, "0");
    const ss = String(safe % 60).padStart(2, "0");
    return `~${mm}:${ss}`;
  };

  const updateStepUi = (stepIndex) => {
    if (!progressSteps) return;
    progressSteps.querySelectorAll("li").forEach((item) => {
      const itemStep = Number(item.dataset.step || 0);
      item.classList.toggle("active", itemStep <= stepIndex);
    });
  };

  const updateProgressUi = () => {
    const timeline = getCurrentTimeline();
    const stage = timeline.find((item) => progress <= item.limit) || timeline[timeline.length - 1];
    const elapsed = (Date.now() - startTs) / 1000;
    const remaining = progress > 2 ? elapsed * ((100 - progress) / progress) : 180;

    progressBar.style.width = `${progress}%`;
    progressPercent.textContent = `${Math.round(progress)}%`;
    progressStatus.textContent = `${stage.text}…`;
    progressEta.textContent = `Примерное время до окончания: ${formatEta(remaining)}`;
    updateStepUi(stage.step);
  };

  const startProgress = () => {
    progress = 3;
    startTs = Date.now();
    progressIdle.textContent = "Запустите анализ, чтобы увидеть прогресс.";
    progressIdle.classList.add("hidden");
    progressWrap.classList.remove("hidden");
    updateProgressUi();

    progressTimer = window.setInterval(() => {
      if (progress < 92) progress += 1.1;
      updateProgressUi();
    }, 1200);
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
    updateStepUi(5);
    window.setTimeout(() => {
      progressWrap.classList.add("hidden");
      progressIdle.classList.remove("hidden");
      progressIdle.textContent = "Анализ завершён. Можно запускать следующий.";
    }, 700);
  };

  const failProgress = () => {
    if (progressTimer) {
      clearInterval(progressTimer);
      progressTimer = null;
    }
    progressStatus.textContent = "Анализ завершился с ошибкой";
    progressEta.textContent = "Проверьте сообщение об ошибке в отчёте";
    window.setTimeout(() => {
      progressWrap.classList.add("hidden");
      progressIdle.classList.remove("hidden");
      progressIdle.textContent = "Анализ завершился с ошибкой. Исправьте данные и повторите.";
    }, 700);
  };

  const updateTopNInPrompt = (text, n) => {
    return text
      .replace(/топ-\d+/gi, `топ-${n}`)
      .replace(/топ-страниц(?:[а-я]*)?/gi, `топ-${n} страниц`)
      .replace(/топовые\s+страниц(?:ы|а|е|у|ой|ам|ами|ах)?/gi, `топ-${n} страниц`)
      .replace(/топовых\s+страниц(?:ы|а|е|у|ой|ам|ами|ах)?/gi, `топ-${n} страниц`);
  };

  const applyTopN = (value) => {
    const n = Math.max(1, Math.min(30, Number(value) || 1));
    topNValue.value = String(n);
    if (metricsPromptField) metricsPromptField.value = updateTopNInPrompt(metricsPromptField.value, n);
    if (audiencePromptField) audiencePromptField.value = updateTopNInPrompt(audiencePromptField.value, n);
    if (pagesPromptField) pagesPromptField.value = updateTopNInPrompt(pagesPromptField.value, n);
  };

  const syncTopNMode = () => {
    if (!topNMode || !customTopN) return;
    if (topNMode.value === "custom") {
      customTopN.classList.remove("hidden");
      applyTopN(customTopN.value);
    } else {
      customTopN.classList.add("hidden");
      applyTopN(1);
    }
  };

  const syncAuditMode = () => {
    if (!auditMode) return;
    const isFull = auditMode.value === "full";
    fullOnlyBlocks.forEach((el) => el.classList.toggle("hidden", !isFull));
    screenshotOnlyBlocks.forEach((el) => el.classList.toggle("hidden", isFull));

    if (filesInput) filesInput.required = isFull;
    if (pageUrlInput) pageUrlInput.required = !isFull;

    if (!isFull) {
      topNMode.value = "top1";
      customTopN.classList.add("hidden");
      applyTopN(1);
    } else {
      syncTopNMode();
    }

    if (metricsSection) {
      metricsSection.classList.toggle("hidden", !isFull);
    }
  };

  const renderFileChips = () => {
    if (!filesInput || !filesCount || !filesChips) return;
    const files = Array.from(filesInput.files || []);
    filesCount.textContent = `Выбрано: ${files.length}`;
    filesChips.replaceChildren();
    files.slice(0, 12).forEach((file) => {
      const chip = document.createElement("span");
      chip.className = "chip";
      chip.textContent = file.name;
      filesChips.appendChild(chip);
    });
  };

  const togglePromptEditor = (targetId, button) => {
    const target = document.getElementById(targetId);
    if (!target) return;
    const isHidden = target.classList.contains("hidden");

    [rolePromptField, metricsPromptField, audiencePromptField, pagesPromptField].forEach((field) => {
      if (!field || field.id === targetId) return;
      field.classList.add("hidden");
    });

    promptToggles.forEach((btn) => {
      if (btn === button) return;
      btn.textContent = "Редактировать";
    });

    target.classList.toggle("hidden", !isHidden);
    button.textContent = isHidden ? "Свернуть" : "Редактировать";
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

    const visits = document.createElement("p");
    visits.innerHTML = `<b>Визиты:</b> ${page.visits}`;

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
    article.appendChild(visits);
    article.appendChild(pageTitle);
    article.appendChild(details);
    article.appendChild(shots);

    return article;
  };

  const renderResult = (result) => {
    lastResult = result;
    emptyState.classList.add("hidden");
    resultView.classList.remove("hidden");

    let domain = "-";
    try {
      const firstUrl = Array.isArray(result.top_pages) && result.top_pages.length > 0 ? result.top_pages[0].url : "";
      domain = firstUrl ? new URL(firstUrl).hostname : "-";
    } catch (error) {
      domain = "-";
    }

    runIdTitle.textContent = `Отчёт для сайта ${domain}`;

    const pagesCount = Array.isArray(result.top_pages) ? result.top_pages.length : 0;
    const sourceLabel = result.audit_mode === "screenshot" ? "Источник: Ссылка" : "Источник: Метрика";
    reportMeta.textContent = `Страниц: ${pagesCount} • ${sourceLabel}`;
    reportMeta.classList.remove("hidden");

    finalSummary.textContent = result.final_summary || "";
    summarySection.classList.toggle("hidden", !result.final_summary);

    metricsAnalysis.textContent = result.metrics_analysis || "";
    metricsSection.classList.toggle("hidden", result.audit_mode === "screenshot");

    audienceAnalysis.textContent = result.audience_analysis || "";
    pagesAnalysis.textContent = result.pages_analysis || "";

    pagesGrid.replaceChildren();
    (result.top_pages || []).forEach((item) => pagesGrid.appendChild(createPageCard(item)));

    if (result.errors && result.errors.length > 0) {
      errorsBox.classList.remove("hidden");
      errorsBox.replaceChildren();
      result.errors.forEach((err) => {
        const p = document.createElement("p");
        p.textContent = err;
        errorsBox.appendChild(p);
      });
    } else {
      errorsBox.classList.add("hidden");
      errorsBox.replaceChildren();
    }
  };

  const buildReportText = (result) => {
    const lines = [];
    lines.push(`Отчёт: ${result.run_id || "-"}`);
    lines.push(`Режим: ${result.audit_mode || "-"}`);
    lines.push("");
    if (result.final_summary) {
      lines.push("Саммари");
      lines.push(result.final_summary);
      lines.push("");
    }
    if (result.metrics_analysis) {
      lines.push("Анализ метрики");
      lines.push(result.metrics_analysis);
      lines.push("");
    }
    if (result.audience_analysis) {
      lines.push("Анализ ЦА / JTBD");
      lines.push(result.audience_analysis);
      lines.push("");
    }
    if (result.pages_analysis) {
      lines.push("Анализ страниц и скриншотов");
      lines.push(result.pages_analysis);
      lines.push("");
    }
    return lines.join("\n");
  };

  if (headerMoreToggle && headerMore) {
    headerMoreToggle.addEventListener("click", () => {
      const hidden = headerMore.classList.contains("hidden");
      headerMore.classList.toggle("hidden", !hidden);
            headerMoreToggle.setAttribute("aria-expanded", String(hidden));
    });
  }

  if (filePickerBtn && filesInput) {
    filePickerBtn.addEventListener("click", () => filesInput.click());
    filesInput.addEventListener("change", renderFileChips);
    renderFileChips();
  }

  if (filesHelpToggle && filesHelp) {
    filesHelpToggle.addEventListener("click", (event) => {
      event.preventDefault();
      const hidden = filesHelp.classList.contains("hidden");
      filesHelp.classList.toggle("hidden", !hidden);
      filesHelpToggle.textContent = hidden ? "Свернуть" : "Какие файлы надо";
    });
  }

  promptToggles.forEach((btn) => {
    btn.addEventListener("click", () => {
      const targetId = btn.dataset.target;
      if (!targetId) return;
      togglePromptEditor(targetId, btn);
    });
  });

  if (topNMode && customTopN && topNValue) {
    topNMode.addEventListener("change", syncTopNMode);
    customTopN.addEventListener("input", () => applyTopN(customTopN.value));
    syncTopNMode();
  }

  if (auditMode) {
    auditMode.addEventListener("change", syncAuditMode);
    syncAuditMode();
  }

  if (copyReportBtn) {
    copyReportBtn.addEventListener("click", async () => {
      if (!lastResult) return;
      try {
        await navigator.clipboard.writeText(buildReportText(lastResult));
        copyReportBtn.textContent = "Скопировано";
        setTimeout(() => {
          copyReportBtn.textContent = "Копировать";
        }, 1400);
      } catch (error) {
        copyReportBtn.textContent = "Ошибка";
        setTimeout(() => {
          copyReportBtn.textContent = "Копировать";
        }, 1400);
      }
    });
  }

  if (downloadPdfBtn) {
    downloadPdfBtn.addEventListener("click", async () => {
      if (!lastResult) return;
      downloadPdfBtn.disabled = true;
      downloadPdfBtn.textContent = "Готовим PDF…";
      try {
        const response = await fetch("/report/pdf", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(lastResult),
        });
        if (!response.ok) throw new Error("Не удалось сформировать PDF");

        const blob = await response.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `audit_report_${lastResult.run_id || "report"}.pdf`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
      } catch (error) {
        downloadPdfBtn.textContent = "Ошибка PDF";
        setTimeout(() => {
          downloadPdfBtn.textContent = "В PDF";
        }, 1500);
      } finally {
        downloadPdfBtn.disabled = false;
        if (downloadPdfBtn.textContent !== "Ошибка PDF") {
          downloadPdfBtn.textContent = "В PDF";
        }
      }
    });
  }

  if (exportSheetsBtn) {
    exportSheetsBtn.addEventListener("click", async () => {
      if (!lastResult) return;
      exportSheetsBtn.disabled = true;
      exportSheetsBtn.textContent = "Экспортируем…";
      try {
        const response = await fetch("/export/google-sheets", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            report_type: "site",
            payload: lastResult,
          }),
        });
        const payload = await response.json();
        if (!response.ok || (payload.errors && payload.errors.length > 0)) {
          throw new Error((payload.errors || []).join("; ") || "Ошибка экспорта");
        }
        window.open(payload.spreadsheet_url, "_blank", "noopener,noreferrer");
        exportSheetsBtn.textContent = "Готово";
      } catch (error) {
        exportSheetsBtn.textContent = "Ошибка";
      } finally {
        setTimeout(() => {
          exportSheetsBtn.disabled = false;
          exportSheetsBtn.textContent = "В Google Sheets";
        }, 1500);
      }
    });
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();

    const formData = new FormData(form);
    submitBtn.disabled = true;
    startProgress();

    try {
      const response = await fetch("/analyze", {
        method: "POST",
        body: formData,
      });

      const payload = await response.json();
      renderResult(payload);

      if (!response.ok || (payload.errors && payload.errors.length > 0)) {
        failProgress();
      } else {
        completeProgress();
      }
    } catch (error) {
      renderResult({
        run_id: "-",
        audit_mode: auditMode ? auditMode.value : "-",
        final_summary: "",
        metrics_analysis: "",
        audience_analysis: "",
        pages_analysis: "",
        top_pages: [],
        errors: [String(error)],
      });
      failProgress();
    } finally {
      submitBtn.disabled = false;
    }
  });
})();
