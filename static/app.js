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

  const reportDocument = document.getElementById("report-document");
  const reportToc = document.getElementById("report-toc");

  const headerMoreToggle = document.getElementById("header-more-toggle");
  const headerMore = document.getElementById("header-more");

  const auditMode = document.getElementById("audit-mode");
  const fullOnlyBlocks = document.querySelectorAll(".full-only");
  const metrikaOnlyBlocks = document.querySelectorAll(".metrika-only");
  const metricsModeOnlyBlocks = document.querySelectorAll(".metrics-mode-only");
  const screenshotOnlyBlocks = document.querySelectorAll(".screenshot-only");
  const metrikaCounterSelect = document.getElementById("metrika-counter-id");
  const metrikaCounterStatus = document.getElementById("metrika-counter-status");

  const filesInput = document.getElementById("files-input");
  const filePickerBtn = document.getElementById("file-picker-btn");
  const filesCount = document.getElementById("files-count");
  const filesChips = document.getElementById("files-chips");
  const filesHelpToggle = document.getElementById("files-help-toggle");
  const filesHelp = document.getElementById("files-help");

  const pageUrlInput = document.getElementById("page-url");
  const auditModeHelpText = document.getElementById("audit-mode-help-text");

  const topNMode = document.getElementById("top-n-mode");
  const customTopN = document.getElementById("custom-top-n");
  const topNValue = document.getElementById("top-n-value");

  const promptDefaultsNode = document.getElementById("site-prompts-defaults");
  const promptDefaults = (() => {
    if (!promptDefaultsNode) return {};
    try {
      return JSON.parse(promptDefaultsNode.textContent || "{}");
    } catch (error) {
      return {};
    }
  })();
  const promptFields = Array.from(document.querySelectorAll(".prompt-field"));
  const promptToggles = document.querySelectorAll(".prompt-toggle");
  const promptResetButtons = document.querySelectorAll(".prompt-reset");
  const promptValidateButtons = document.querySelectorAll(".prompt-validate");

  const modeHelpMap = {
    screenshot: "Аудит одной страницы: скриншоты, текст, ЦА/JTBD и рекомендации.",
    full: "Анализ по Excel-выгрузкам Метрики: выбор топ-страниц, скриншоты и итоговый аудит.",
    metrika: "Анализ напрямую по API Яндекс.Метрики: выбор счётчика, топ-страницы и полный аудит без Excel.",
  };

  const reportSectionTitles = [
    "0) Паспорт отчёта",
    "1) Саммари",
    "2) Данные: диагностика спроса и входов",
    "3) Экспертный UX-аудит по скриншотам",
    "4) Карта соответствия «интенты → посадочные → UX-узкие места»",
    "5) План действий и контроль результата",
    "6) Приложения",
  ];

  let progressTimer = null;
  let progress = 0;
  let startTs = 0;
  let lastResult = null;
  let reportBundle = { report_md: "", report_text: "", sections: [] };
  let metrikaCountersLoaded = false;
  let tocObserver = null;

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

  const metrikaStageTimeline = [
    { limit: 25, text: "Этап 1/5: загрузка данных из API Метрики", step: 1 },
    { limit: 45, text: "Этап 2/5: выбор страниц", step: 2 },
    { limit: 67, text: "Этап 3/5: скриншоты", step: 3 },
    { limit: 84, text: "Этап 4/5: извлечение текста", step: 4 },
    { limit: 98, text: "Этап 5/5: рекомендации", step: 5 },
  ];

  const getCurrentTimeline = () => {
    if (!auditMode) return fullStageTimeline;
    if (auditMode.value === "screenshot") return screenshotStageTimeline;
    if (auditMode.value === "metrika") return metrikaStageTimeline;
    return fullStageTimeline;
  };

  const formatEta = (seconds) => {
    const safe = Math.max(0, Math.round(seconds));
    const mm = String(Math.floor(safe / 60)).padStart(2, "0");
    const ss = String(safe % 60).padStart(2, "0");
    return `~${mm}:${ss}`;
  };

  const escapeHtml = (value) =>
    String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/\"/g, "&quot;")
      .replace(/'/g, "&#39;");

  const cleanAiText = (text) => {
    const source = String(text || "").replace(/\r/g, "");
    const lines = source.split("\n");
    const cleaned = lines
      .filter((line) => !/^\s*<\/?[A-Z0-9_\-]+[^>]*>\s*$/.test(line.trim()))
      .filter((line) => !/^\s*```/.test(line.trim()));
    return cleaned.join("\n").trim();
  };

  const shortenUrl = (url) => {
    try {
      const parsed = new URL(url);
      const short = `${parsed.hostname}${parsed.pathname}`;
      return short.length > 72 ? `${short.slice(0, 69)}...` : short;
    } catch (error) {
      return url.length > 72 ? `${url.slice(0, 69)}...` : url;
    }
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
    const section2Prompt = document.getElementById("prompt-section-2");
    const section1Prompt = document.getElementById("prompt-section-1");
    if (section2Prompt) section2Prompt.value = updateTopNInPrompt(section2Prompt.value, n);
    if (section1Prompt) section1Prompt.value = updateTopNInPrompt(section1Prompt.value, Math.min(5, n));
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
    const isMetrika = auditMode.value === "metrika";
    const isScreenshot = auditMode.value === "screenshot";
    fullOnlyBlocks.forEach((el) => el.classList.toggle("hidden", !isFull));
    metrikaOnlyBlocks.forEach((el) => el.classList.toggle("hidden", !isMetrika));
    metricsModeOnlyBlocks.forEach((el) => el.classList.toggle("hidden", isScreenshot));
    screenshotOnlyBlocks.forEach((el) => el.classList.toggle("hidden", !isScreenshot));

    if (filesInput) filesInput.required = isFull;
    if (pageUrlInput) pageUrlInput.required = isScreenshot;
    if (metrikaCounterSelect) metrikaCounterSelect.required = isMetrika;

    if (isScreenshot) {
      topNMode.value = "top1";
      customTopN.classList.add("hidden");
      applyTopN(1);
    } else {
      syncTopNMode();
    }

    if (auditModeHelpText) {
      auditModeHelpText.textContent = modeHelpMap[auditMode.value] || "";
    }

    if (isMetrika && !metrikaCountersLoaded) {
      loadMetrikaCounters();
    }
  };

  const loadMetrikaCounters = async () => {
    if (!metrikaCounterSelect) return;
    metrikaCounterSelect.innerHTML = "<option value=''>Загрузка счётчиков…</option>";
    if (metrikaCounterStatus) {
      metrikaCounterStatus.classList.add("hidden");
      metrikaCounterStatus.textContent = "";
    }

    try {
      const response = await fetch("/metrika/counters");
      const payload = await response.json();
      const items = Array.isArray(payload.items) ? payload.items : [];
      metrikaCounterSelect.replaceChildren();

      const defaultOption = document.createElement("option");
      defaultOption.value = "";
      defaultOption.textContent = items.length > 0 ? "Выберите счётчик" : "Счётчики не найдены";
      metrikaCounterSelect.appendChild(defaultOption);

      items.forEach((item) => {
        const option = document.createElement("option");
        option.value = String(item.id);
        option.textContent = `${item.name} (${item.id})${item.site ? ` — ${item.site}` : ""}`;
        metrikaCounterSelect.appendChild(option);
      });

      if (!response.ok || payload.error) {
        throw new Error(payload.error || "Не удалось загрузить счётчики");
      }
      metrikaCountersLoaded = true;
    } catch (error) {
      metrikaCountersLoaded = false;
      if (metrikaCounterStatus) {
        metrikaCounterStatus.textContent = `Ошибка загрузки счётчиков: ${String(error)}`;
        metrikaCounterStatus.classList.remove("hidden");
      }
      if (metrikaCounterSelect.options.length === 0) {
        const failedOption = document.createElement("option");
        failedOption.value = "";
        failedOption.textContent = "Не удалось загрузить";
        metrikaCounterSelect.appendChild(failedOption);
      }
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
    const actions = document.querySelector(`[data-actions-for="${targetId}"]`);
    const isHidden = target.classList.contains("hidden");

    promptFields.forEach((field) => {
      if (!field || field.id === targetId) return;
      field.classList.add("hidden");
      const fieldActions = document.querySelector(`[data-actions-for="${field.id}"]`);
      if (fieldActions) fieldActions.classList.add("hidden");
    });

    promptToggles.forEach((btn) => {
      if (btn === button) return;
      btn.textContent = "Редактировать";
    });

    target.classList.toggle("hidden", !isHidden);
    if (actions) actions.classList.toggle("hidden", !isHidden);
    button.textContent = isHidden ? "Свернуть" : "Редактировать";
  };

  const markdownInline = (text) => {
    let html = escapeHtml(text);
    html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
    html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    html = html.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, (_m, label, href) => {
      const full = String(href);
      const shortLabel = label === href ? shortenUrl(full) : label;
      return `<a class=\"doc-link\" href=\"${escapeHtml(full)}\" target=\"_blank\" rel=\"noreferrer\" title=\"${escapeHtml(full)}\" data-full-url=\"${escapeHtml(full)}\">${escapeHtml(shortLabel)}</a><button class=\"url-copy-btn\" type=\"button\" data-full-url=\"${escapeHtml(full)}\">коп.</button>`;
    });
    html = html.replace(/(https?:\/\/[^\s<]+)/g, (href) => {
      const full = String(href);
      const short = shortenUrl(full);
      return `<a class=\"doc-link\" href=\"${escapeHtml(full)}\" target=\"_blank\" rel=\"noreferrer\" title=\"${escapeHtml(full)}\" data-full-url=\"${escapeHtml(full)}\">${escapeHtml(short)}</a><button class=\"url-copy-btn\" type=\"button\" data-full-url=\"${escapeHtml(full)}\">коп.</button>`;
    });
    return html;
  };

  const markdownBodyToHtml = (md) => {
    const lines = String(md || "").replace(/\r/g, "").split("\n");
    const out = [];
    let inUl = false;
    let inOl = false;

    const closeLists = () => {
      if (inUl) {
        out.push("</ul>");
        inUl = false;
      }
      if (inOl) {
        out.push("</ol>");
        inOl = false;
      }
    };

    lines.forEach((raw) => {
      const line = raw.trim();
      if (!line) {
        closeLists();
        return;
      }

      const h3 = line.match(/^###\s+(.+)$/);
      if (h3) {
        closeLists();
        out.push(`<h3>${markdownInline(h3[1])}</h3>`);
        return;
      }

      const ul = line.match(/^[-•]\s+(.+)$/);
      if (ul) {
        if (inOl) {
          out.push("</ol>");
          inOl = false;
        }
        if (!inUl) {
          out.push("<ul>");
          inUl = true;
        }
        out.push(`<li>${markdownInline(ul[1])}</li>`);
        return;
      }

      const ol = line.match(/^\d+\.\s+(.+)$/);
      if (ol) {
        if (inUl) {
          out.push("</ul>");
          inUl = false;
        }
        if (!inOl) {
          out.push("<ol>");
          inOl = true;
        }
        out.push(`<li>${markdownInline(ol[1])}</li>`);
        return;
      }

      closeLists();
      out.push(`<p>${markdownInline(line)}</p>`);
    });

    closeLists();
    return out.join("\n");
  };

  const markdownToPlain = (md) =>
    String(md || "")
      .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, "$1 ($2)")
      .replace(/`([^`]+)`/g, "$1")
      .replace(/\*\*([^*]+)\*\*/g, "$1")
      .replace(/\r/g, "")
      .replace(/\n{3,}/g, "\n\n")
      .trim();

  const slugifySection = (title) =>
    String(title || "")
      .toLowerCase()
      .replace(/<[^>]+>/g, "")
      .replace(/[^a-zа-яё0-9]+/gi, "-")
      .replace(/^-+|-+$/g, "") || "section";

  const parseMarkdownSections = (reportMd) => {
    const lines = String(reportMd || "").replace(/\r/g, "").split("\n");
    const sections = [];
    let current = null;
    lines.forEach((line) => {
      const h2 = line.match(/^##\s+(.+)$/);
      if (h2) {
        if (current) sections.push(current);
        const title = h2[1].trim();
        current = {
          id: `section-${slugifySection(title)}`,
          title,
          md: "",
        };
        return;
      }
      if (current) {
        current.md += `${line}\n`;
      }
    });
    if (current) sections.push(current);
    return sections.map((section) => ({ ...section, md: section.md.trim() }));
  };

  const buildStructuredReport = (result) => {
    const reportMd = cleanAiText(result.report_md || "");
    const sectionsFromMd = parseMarkdownSections(reportMd);
    if (reportMd && sectionsFromMd.length > 0) {
      return {
        sections: sectionsFromMd,
        report_md: reportMd,
        report_text: markdownToPlain(reportMd),
      };
    }

    const fallbackSections = [];
    const map = result.report_sections || {};
    ["0", "1", "2", "3", "4", "5", "6"].forEach((key) => {
      const value = cleanAiText(map[key] || "");
      if (value) {
        const title = value.match(/^##\s+(.+)$/m)?.[1] || reportSectionTitles[Number(key)] || `Раздел ${key}`;
        fallbackSections.push({
          id: `section-${slugifySection(title)}`,
          title,
          md: value.replace(/^##\s+.+$/m, "").trim(),
        });
      }
    });

    if (fallbackSections.length > 0) {
      const fallbackMd = fallbackSections
        .map((section) => `## ${section.title}\n\n${section.md}`)
        .join("\n\n");
      return {
        sections: fallbackSections,
        report_md: fallbackMd,
        report_text: markdownToPlain(fallbackMd),
      };
    }

    return { sections: [], report_md: "", report_text: "" };
  };

  const renderReport = (bundle) => {
    if (!reportDocument || !reportToc) return;

    const html = bundle.sections
      .map((section) => {
        const body = markdownBodyToHtml(section.md);
        return `<section id=\"${section.id}\" class=\"report-section\"><h2>${escapeHtml(section.title)}</h2>${body}</section>`;
      })
      .join("\n");

    reportDocument.innerHTML = html;

    const tocHtml = bundle.sections
      .map((section) => `<a href=\"#${section.id}\" class=\"toc-link\" data-target=\"${section.id}\">${escapeHtml(section.title)}</a>`)
      .join("\n");
    reportToc.innerHTML = tocHtml;

    if (tocObserver) tocObserver.disconnect();
    const tocLinks = Array.from(reportToc.querySelectorAll(".toc-link"));
    const sections = Array.from(reportDocument.querySelectorAll(".report-section"));

    const setActiveToc = (id) => {
      tocLinks.forEach((link) => link.classList.toggle("active", link.dataset.target === id));
    };

    tocLinks.forEach((link) => {
      link.addEventListener("click", () => {
        const id = link.dataset.target || "";
        if (id) {
          setActiveToc(id);
        }
      });
    });

    if (sections.length > 0) {
      setActiveToc(sections[0].id);
      tocObserver = new IntersectionObserver(
        (entries) => {
          const visible = entries
            .filter((entry) => entry.isIntersecting)
            .sort((a, b) => b.intersectionRatio - a.intersectionRatio);
          if (visible.length > 0) {
            setActiveToc(visible[0].target.id);
          }
        },
        { rootMargin: "-18% 0px -62% 0px", threshold: [0.15, 0.3, 0.5] },
      );
      sections.forEach((section) => tocObserver.observe(section));
    }

    reportDocument.querySelectorAll(".url-copy-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const full = btn.getAttribute("data-full-url") || "";
        try {
          await navigator.clipboard.writeText(full);
          const prev = btn.textContent;
          btn.textContent = "ok";
          setTimeout(() => {
            btn.textContent = prev || "коп.";
          }, 900);
        } catch (error) {
          btn.textContent = "err";
          setTimeout(() => {
            btn.textContent = "коп.";
          }, 900);
        }
      });
    });
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
    let sourceLabel = "Источник: Метрика";
    if (result.audit_mode === "screenshot") {
      sourceLabel = "Источник: Ссылка";
    } else if (result.audit_mode === "metrika") {
      const counterName = result.metrika_counter_name ? ` (${result.metrika_counter_name})` : "";
      const days = Number(result.metrika_effective_period_days || 0);
      const accuracy = String(result.metrika_effective_accuracy || "");
      const tuning = days > 0 || accuracy ? ` • период: ${days || "-"} дн. • accuracy: ${accuracy || "-"}` : "";
      sourceLabel = `Источник: Метрика API${counterName}${tuning}`;
    } else if (result.audit_mode === "full") {
      sourceLabel = "Источник: Метрика (Excel)";
    }

    reportMeta.textContent = `Страниц: ${pagesCount} • ${sourceLabel}`;
    reportMeta.classList.remove("hidden");

    if (exportSheetsBtn) {
      exportSheetsBtn.classList.toggle("hidden", result.audit_mode === "metrika");
    }

    reportBundle = buildStructuredReport(result);
    renderReport(reportBundle);

    const errors = Array.isArray(result.errors) ? [...result.errors] : [];
    const violationsText = String(result.violations || "").trim();
    if (errors.length > 0 || violationsText) {
      errorsBox.classList.remove("hidden");
      errorsBox.replaceChildren();
      errors.forEach((err) => {
        const p = document.createElement("p");
        p.textContent = err;
        errorsBox.appendChild(p);
      });
      if (violationsText) {
        const wrapper = document.createElement("div");
        wrapper.className = "technical-notes";

        const title = document.createElement("strong");
        title.textContent = "Технические заметки";
        wrapper.appendChild(title);

        const notes = document.createElement("p");
        notes.className = "technical-notes-text truncated";
        notes.textContent = violationsText;
        wrapper.appendChild(notes);

        const toggle = document.createElement("button");
        toggle.type = "button";
        toggle.className = "link-subtle technical-notes-toggle";
        toggle.textContent = "показать полностью";
        toggle.addEventListener("click", () => {
          const truncated = notes.classList.contains("truncated");
          notes.classList.toggle("truncated", !truncated);
          toggle.textContent = truncated ? "свернуть" : "показать полностью";
        });
        wrapper.appendChild(toggle);

        errorsBox.appendChild(wrapper);
      }
    } else {
      errorsBox.classList.add("hidden");
      errorsBox.replaceChildren();
    }
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

  promptResetButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      const key = btn.dataset.key || "";
      const targetId = btn.dataset.target || "";
      const target = document.getElementById(targetId);
      if (!target || !Object.prototype.hasOwnProperty.call(promptDefaults, key)) return;
      target.value = String(promptDefaults[key] || "");
      const status = document.querySelector(`[data-status-for="${targetId}"]`);
      if (status) {
        status.textContent = "Сброшено к шаблону";
        status.className = "prompt-validate-status ok";
      }
    });
  });

  promptValidateButtons.forEach((btn) => {
    btn.addEventListener("click", async () => {
      const key = btn.dataset.key || "";
      const targetId = btn.dataset.target || "";
      const target = document.getElementById(targetId);
      const status = document.querySelector(`[data-status-for="${targetId}"]`);
      if (!target || !status) return;

      status.textContent = "Проверяю…";
      status.className = "prompt-validate-status";
      btn.disabled = true;
      try {
        const response = await fetch("/prompt/validate", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            key,
            prompt: target.value || "",
            audit_mode: auditMode ? auditMode.value : "screenshot",
            top_n: Number(topNValue?.value || 1),
          }),
        });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.error || "Ошибка проверки");
        if (payload.status === "warn") {
          status.textContent = `Ок, но есть риски: ${(payload.warnings || []).join("; ")}`;
          status.className = "prompt-validate-status warn";
        } else {
          status.textContent = "Промт прошёл проверку";
          status.className = "prompt-validate-status ok";
        }
      } catch (error) {
        status.textContent = `Ошибка проверки: ${String(error)}`;
        status.className = "prompt-validate-status err";
      } finally {
        btn.disabled = false;
      }
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
      if (!reportBundle.report_text) return;
      try {
        await navigator.clipboard.writeText(reportBundle.report_text);
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
          body: JSON.stringify({ ...lastResult, report_md: reportBundle.report_md, report_text: reportBundle.report_text }),
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
    form.querySelectorAll('input[type="checkbox"][name^="enabled_"]').forEach((checkbox) => {
      formData.set(checkbox.name, checkbox.checked ? "1" : "0");
    });
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
