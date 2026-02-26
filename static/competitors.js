(() => {
  const form = document.getElementById("competitors-form");
  if (!form) return;

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

  const summaryDoc = document.getElementById("comp-summary-doc");
  const blocksDoc = document.getElementById("comp-normalized-blocks-doc");
  const meaningsDoc = document.getElementById("comp-analysis-meanings-doc");

  const generalToc = document.getElementById("comp-general-toc");
  const blocksToc = document.getElementById("comp-blocks-toc");
  const meaningsToc = document.getElementById("comp-meanings-toc");

  const pagesGrid = document.getElementById("comp-pages-grid");
  const exportSheetsBtn = document.getElementById("comp-export-sheets-btn");

  const promptDefaultsNode = document.getElementById("competitor-prompts-defaults");
  const promptDefaults = (() => {
    if (!promptDefaultsNode) return {};
    try {
      return JSON.parse(promptDefaultsNode.textContent || "{}");
    } catch (_error) {
      return {};
    }
  })();

  const promptFields = Array.from(document.querySelectorAll(".prompt-field"));
  const promptToggles = document.querySelectorAll(".prompt-toggle");
  const promptResetButtons = document.querySelectorAll(".prompt-reset");
  const promptValidateButtons = document.querySelectorAll(".prompt-validate");

  const tabButtons = document.querySelectorAll(".tab-btn");
  const tabPanels = document.querySelectorAll(".tab-panel");

  let progressTimer = null;
  let progress = 0;
  let startTs = 0;
  let lastEtaSeconds = null;
  let etaBecameWorse = false;
  let lastResult = null;
  const docObservers = new Map();

  const formatEta = (seconds) => {
    const safe = Math.max(0, Math.round(seconds));
    const mm = String(Math.floor(safe / 60)).padStart(2, "0");
    const ss = String(safe % 60).padStart(2, "0");
    return `~${mm}:${ss}`;
  };

  const updateProgress = () => {
    const elapsed = (Date.now() - startTs) / 1000;
    const remaining = progress > 2 ? elapsed * ((100 - progress) / progress) : 180;
    let status = "Этап 1/4: сбор страниц";
    if (progress >= 24) status = "Этап 2/4: скриншоты и текст";
    if (progress >= 54) status = "Этап 3/4: блоки и смыслы";
    if (progress >= 78) status = "Этап 4/4: общие выводы и экспорт";

    progressBar.style.width = `${progress}%`;
    progressPercent.textContent = `${Math.round(progress)}%`;
    progressStatus.textContent = `${status}…`;

    if (lastEtaSeconds !== null && remaining > lastEtaSeconds + 1) etaBecameWorse = true;
    lastEtaSeconds = remaining;
    progressEta.textContent = etaBecameWorse
      ? "Подожди ещё чутка по-братски, что-то туго идёт"
      : `Примерное время до окончания: ${formatEta(remaining)}`;
  };

  const startProgress = () => {
    progress = 3;
    startTs = Date.now();
    lastEtaSeconds = null;
    etaBecameWorse = false;
    progressIdle.classList.add("hidden");
    progressWrap.classList.remove("hidden");
    updateProgress();

    progressTimer = window.setInterval(() => {
      if (progress < 92) progress += 1.05;
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

  const renderErrors = (errors, options = {}) => {
    if (!errorsBox) return;
    const list = Array.isArray(errors) ? errors.filter(Boolean) : [];
    if (list.length === 0) {
      errorsBox.classList.add("hidden");
      errorsBox.replaceChildren();
      return;
    }
    const shortText = options.short || list[0];
    errorsBox.classList.remove("hidden");
    errorsBox.replaceChildren();

    const shortNode = document.createElement("p");
    shortNode.textContent = shortText;
    errorsBox.appendChild(shortNode);

    if (list.length > 1 || (list[0] && list[0] !== shortText)) {
      const details = document.createElement("details");
      details.className = "technical-notes";
      const summary = document.createElement("summary");
      summary.textContent = "Показать детали";
      details.appendChild(summary);
      list.forEach((item) => {
        const line = document.createElement("p");
        line.className = "technical-notes-text";
        line.textContent = item;
        details.appendChild(line);
      });
      errorsBox.appendChild(details);
    }
  };

  const escapeHtml = (value) =>
    String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/\"/g, "&quot;")
      .replace(/'/g, "&#39;");

  const markdownInline = (text) => {
    let html = escapeHtml(text);
    html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
    html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    html = html.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a class="doc-link" href="$2" target="_blank" rel="noreferrer" title="$2">$1</a>');
    html = html.replace(/(https?:\/\/[^\s<]+)/g, '<a class="doc-link" href="$1" target="_blank" rel="noreferrer" title="$1">$1</a>');
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

  const prettifyTaxonomyText = (text) => {
    let value = String(text || "");
    value = value.replace(/L1\s*→\s*L2\s*→\s*L3/gi, "категория блока / тип блока / подтип блока");
    value = value.replace(/\bL1\/L2\/L3\b/gi, "категория блока / тип блока / подтип блока");
    value = value.replace(/^\s*L1\s*:/gim, "категория блока:");
    value = value.replace(/^\s*L2\s*:/gim, "тип блока:");
    value = value.replace(/^\s*L3\s*:/gim, "подтип блока:");
    return value;
  };

  const splitSections = (text, fallbackTitle) => {
    const lines = String(text || "").replace(/\r/g, "").split("\n");
    const sections = [];
    let current = { title: fallbackTitle, lines: [] };

    lines.forEach((line) => {
      const h2 = line.match(/^##\s+(.+)$/);
      if (h2) {
        if (current.lines.length > 0 || current.title) sections.push(current);
        current = { title: h2[1], lines: [] };
      } else {
        current.lines.push(line);
      }
    });
    sections.push(current);
    return sections.filter((section) => section.title || section.lines.join("").trim());
  };

  const renderDoc = (text, tocEl, docEl, fallbackTitle, key) => {
    if (!tocEl || !docEl) return;
    const sections = splitSections(text, fallbackTitle);
    const items = sections.map((section, idx) => ({
      id: `${key}-section-${idx + 1}`,
      title: section.title || `${fallbackTitle} ${idx + 1}`,
      body: section.lines.join("\n").trim(),
    }));

    docEl.innerHTML = items
      .map((item) => `<section id="${item.id}" class="report-section"><h2>${escapeHtml(item.title)}</h2>${markdownBodyToHtml(item.body)}</section>`)
      .join("\n");

    tocEl.innerHTML = items
      .map((item) => `<a href="#${item.id}" class="toc-link" data-target="${item.id}">${escapeHtml(item.title)}</a>`)
      .join("\n");

    const links = Array.from(tocEl.querySelectorAll(".toc-link"));
    const sectionEls = Array.from(docEl.querySelectorAll(".report-section"));
    const setActive = (id) => {
      links.forEach((link) => link.classList.toggle("active", link.dataset.target === id));
    };

    links.forEach((link) => {
      link.addEventListener("click", () => setActive(link.dataset.target || ""));
    });

    const prev = docObservers.get(key);
    if (prev) prev.disconnect();
    if (sectionEls.length === 0) return;

    setActive(sectionEls[0].id);
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries.filter((entry) => entry.isIntersecting).sort((a, b) => b.intersectionRatio - a.intersectionRatio);
        if (visible.length > 0) setActive(visible[0].target.id);
      },
      { rootMargin: "-18% 0px -62% 0px", threshold: [0.15, 0.3, 0.5] },
    );
    sectionEls.forEach((section) => observer.observe(section));
    docObservers.set(key, observer);
  };

  const sectionReasonText = (reason) => {
    const mapping = {
      ok: "ok",
      disabled_by_user: "Секция отключена в расширенных настройках.",
      llm_error: "Ошибка генерации в модели.",
      empty_model_output: "Модель вернула пустой ответ.",
      openai_quota_exceeded: "Превышена квота API OpenAI.",
      openai_rate_limited: "Превышен лимит запросов API OpenAI.",
    };
    return mapping[String(reason || "")] || "Секция не сформирована.";
  };

  const getSectionStatus = (payload, key) => {
    const statuses = payload && payload.section_status ? payload.section_status : {};
    const state = statuses[key] || {};
    return {
      enabled: state.enabled !== false,
      has_content: Boolean(state.has_content),
      reason: state.reason || "ok",
    };
  };

  const ensureSectionText = (payload, key, title, rawText) => {
    const text = String(rawText || "").trim();
    if (text) return prettifyTaxonomyText(text);
    const status = getSectionStatus(payload, key);
    const reason = status.enabled ? status.reason || "empty_model_output" : "disabled_by_user";
    return `## ${title}\n\nРаздел не сформирован: ${sectionReasonText(reason)}`;
  };

  const hasAnySectionContent = (payload) => {
    const statuses = payload && payload.section_status ? payload.section_status : null;
    if (statuses) {
      return ["summary", "blocks", "meanings"].some((key) => statuses[key] && statuses[key].has_content);
    }
    return Boolean(
      String(payload.summary_general || "").trim() ||
      String(payload.normalized_blocks || "").trim() ||
      String(payload.analysis_meanings || "").trim(),
    );
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

  const selectTab = (targetId) => {
    tabButtons.forEach((btn) => {
      const isActive = btn.dataset.target === targetId;
      btn.classList.toggle("active", isActive);
      btn.setAttribute("aria-selected", String(isActive));
    });
    tabPanels.forEach((panel) => {
      panel.classList.toggle("hidden", panel.id !== targetId);
    });
  };

  const renderResult = (payload) => {
    lastResult = payload;
    emptyState.classList.add("hidden");
    resultView.classList.remove("hidden");
    runTitle.textContent = `Отчёт по конкурентам ${payload.run_id || ""}`.trim();

    renderDoc(ensureSectionText(payload, "summary", "Общие выводы", payload.summary_general), generalToc, summaryDoc, "Общие выводы", "comp-general");
    renderDoc(ensureSectionText(payload, "blocks", "Нормализованные блоки", payload.normalized_blocks), blocksToc, blocksDoc, "Нормализованные блоки", "comp-blocks");
    renderDoc(ensureSectionText(payload, "meanings", "Анализ смыслов", payload.analysis_meanings), meaningsToc, meaningsDoc, "Анализ смыслов", "comp-meanings");
    selectTab("panel-general");

    pagesGrid.replaceChildren();
    (payload.pages || []).forEach((p) => pagesGrid.appendChild(createPageCard(p)));

    if (exportSheetsBtn) {
      const ready = Boolean(payload.export_matrix_ready);
      exportSheetsBtn.disabled = !ready;
      exportSheetsBtn.title = ready
        ? ""
        : (payload.export_matrix_reason || "Матрица не сформирована, выгружен текстовый fallback.");
    }

    renderErrors(payload.errors || []);
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

  tabButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      selectTab(btn.dataset.target);
    });
  });

  promptToggles.forEach((btn) => {
    btn.addEventListener("click", () => {
      togglePromptEditor(btn.dataset.target || "", btn);
    });
  });

  promptResetButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      const key = btn.dataset.key || "";
      const targetId = btn.dataset.target || "";
      const field = document.getElementById(targetId);
      const status = document.querySelector(`[data-status-for="${targetId}"]`);
      if (!field || !(field instanceof HTMLTextAreaElement)) return;
      if (Object.prototype.hasOwnProperty.call(promptDefaults, key)) {
        field.value = String(promptDefaults[key] || "");
        if (status) {
          status.textContent = "Сброшено к шаблону";
          status.className = "prompt-validate-status ok";
        }
      }
    });
  });

  promptValidateButtons.forEach((btn) => {
    btn.addEventListener("click", async () => {
      const key = btn.dataset.key || "";
      const targetId = btn.dataset.target || "";
      const field = document.getElementById(targetId);
      const status = document.querySelector(`[data-status-for="${targetId}"]`);
      if (!field || !(field instanceof HTMLTextAreaElement) || !status) return;

      status.textContent = "Проверка…";
      status.className = "prompt-validate-status";
      try {
        const response = await fetch("/prompt/validate", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ key, prompt: field.value }),
        });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.error || "Ошибка проверки");

        const warnings = Array.isArray(payload.warnings) ? payload.warnings : [];
        if (warnings.length > 0) {
          status.textContent = `Проверено: ${warnings.join("; ")}`;
          status.className = "prompt-validate-status warn";
        } else {
          status.textContent = "Проверено: ок";
          status.className = "prompt-validate-status ok";
        }
      } catch (error) {
        status.textContent = `Ошибка: ${String(error)}`;
        status.className = "prompt-validate-status err";
      }
    });
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    submitBtn.disabled = true;
    startProgress();

    try {
      const formData = new FormData(form);
      form.querySelectorAll('input[type="checkbox"][name^="enabled_"]').forEach((checkbox) => {
        const input = checkbox;
        formData.set(input.name, input.checked ? "1" : "0");
      });

      const response = await fetch("/analyze-competitors", {
        method: "POST",
        body: formData,
      });
      const payload = await response.json();
      renderResult(payload);

      const errors = Array.isArray(payload.errors) ? payload.errors.filter(Boolean) : [];
      const hasContent = hasAnySectionContent(payload);
      const exportStage = payload.export_stage_status || { ok: true, error: "" };

      if (!response.ok || (errors.length > 0 && !hasContent)) {
        if (errors.length > 0) renderErrors(errors, { short: "Анализ завершился с ошибкой." });
        failProgress();
      } else {
        if (errors.length > 0) {
          renderErrors(errors, { short: "Анализ завершён с замечаниями." });
        } else if (!payload.export_matrix_ready) {
          const reason = payload.export_matrix_reason || "Матрица не сформирована, выгружен текстовый fallback.";
          const details = exportStage && exportStage.ok === false && exportStage.error
            ? [reason, `Ошибка экспорта таблицы: ${exportStage.error}`]
            : [reason];
          const short = exportStage && exportStage.ok === false
            ? "Ошибка экспорта таблицы"
            : "Матрица не сформирована, выгружен текстовый fallback.";
          renderErrors(details, { short });
        } else {
          renderErrors([]);
        }
        completeProgress();
      }
    } catch (error) {
      renderResult({
        run_id: "",
        pages: [],
        summary_general: "",
        normalized_blocks: "",
        analysis_meanings: "",
        section_status: {
          summary: { enabled: true, has_content: false, reason: "llm_error" },
          blocks: { enabled: true, has_content: false, reason: "llm_error" },
          meanings: { enabled: true, has_content: false, reason: "llm_error" },
        },
        errors: [String(error)],
      });
      renderErrors([String(error)], { short: "Анализ завершился с ошибкой." });
      failProgress();
    } finally {
      submitBtn.disabled = false;
    }
  });

  if (exportSheetsBtn) {
    exportSheetsBtn.addEventListener("click", async () => {
      if (!lastResult) return;
      if (!lastResult.export_matrix_ready) {
        const msg = lastResult.export_matrix_reason || "Матрица не сформирована, выгружен текстовый fallback.";
        renderErrors([msg], { short: "Матрица не сформирована, выгружен текстовый fallback." });
        return;
      }
      exportSheetsBtn.disabled = true;
      exportSheetsBtn.textContent = "Экспортируем…";
      try {
        const response = await fetch("/export/google-sheets", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            report_type: "competitors",
            payload: lastResult,
          }),
        });
        const payload = await response.json();
        if (!response.ok || (payload.errors && payload.errors.length > 0)) {
          throw new Error((payload.errors || []).join("; ") || "Ошибка экспорта");
        }
        if (lastResult && !lastResult.export_matrix_ready && (!lastResult.errors || lastResult.errors.length === 0)) {
          const msg = lastResult.export_matrix_reason || "Матрица не сформирована, выгружен текстовый fallback.";
          renderErrors([msg], { short: "Матрица не сформирована, выгружен текстовый fallback." });
        } else {
          if (!lastResult || !lastResult.errors || lastResult.errors.length === 0) renderErrors([]);
        }
        window.open(payload.spreadsheet_url, "_blank", "noopener,noreferrer");
        exportSheetsBtn.textContent = "Готово";
      } catch (_error) {
        renderErrors([String(_error)], { short: "Ошибка экспорта в таблицы." });
        exportSheetsBtn.textContent = "Ошибка";
      } finally {
        setTimeout(() => {
          exportSheetsBtn.disabled = false;
          exportSheetsBtn.textContent = "Отправить в таблицы";
        }, 1500);
      }
    });
  }
})();
