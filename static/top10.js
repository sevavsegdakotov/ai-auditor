(() => {
  const form = document.getElementById("top10-form");
  if (!form) return;

  const structureMode = document.getElementById("structure-mode");
  const top10SourceBlock = document.getElementById("top10-source-block");
  const queriesField = document.getElementById("top10-queries");
  const queriesFieldLabel = queriesField ? queriesField.closest(".field-label") : null;
  const regionSearchField = document.getElementById("top10-region-search");
  const regionField = document.getElementById("top10-region-id");
  const regionList = document.getElementById("top10-region-list");
  const regionHint = document.getElementById("top10-region-hint");
  const regionFieldLabel = regionSearchField ? regionSearchField.closest(".field-label") : null;
  const urlsField = document.getElementById("top10-urls");
  const fetchStatus = document.getElementById("top10-fetch-status");
  const showBtn = document.getElementById("top10-show-btn");
  const buildBtn = document.getElementById("top10-build-btn");

  const progressWrap = document.getElementById("top10-progress-wrap");
  const progressIdle = document.getElementById("top10-progress-idle");
  const progressBar = document.getElementById("top10-progress-bar");
  const progressPercent = document.getElementById("top10-progress-percent");
  const progressStatus = document.getElementById("top10-progress-status");
  const progressEta = document.getElementById("top10-progress-eta");

  const emptyState = document.getElementById("top10-empty-state");
  const resultView = document.getElementById("top10-result-view");
  const runTitle = document.getElementById("top10-run-title");
  const errorsBox = document.getElementById("top10-errors");

  const urlsUsedDoc = document.getElementById("top10-urls-used-doc");
  const lightOverviewWrap = document.getElementById("top10-light-overview-wrap");
  const lightOverviewDoc = document.getElementById("top10-light-overview-doc");
  const lightOverviewToc = document.getElementById("top10-light-overview-toc");
  const summaryDoc = document.getElementById("top10-summary-doc");
  const blocksDoc = document.getElementById("top10-analysis-structure-doc");
  const structureDoc = document.getElementById("top10-structure-proposal-doc");
  const urlsToc = document.getElementById("top10-urls-toc");
  const summaryToc = document.getElementById("top10-summary-toc");
  const blocksToc = document.getElementById("top10-analysis-toc");
  const structureToc = document.getElementById("top10-structure-toc");

  const pagesGrid = document.getElementById("top10-pages-grid");
  const exportSheetsBtn = document.getElementById("top10-export-sheets-btn");
  const exportSuccessModal = document.getElementById("top10-export-success-modal");
  const exportSuccessClose = document.getElementById("top10-export-success-close");

  const promptDefaultsNode = document.getElementById("top10-prompts-defaults");
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

  const tabButtons = document.querySelectorAll(".top10-tab-btn");
  const tabPanels = document.querySelectorAll("#top10-panel-summary, #top10-panel-blocks, #top10-panel-structure");

  let progressTimer = null;
  let progress = 0;
  let startTs = 0;
  let lastEtaSeconds = null;
  let etaBecameWorse = false;
  let regionFetchTimer = null;
  const regionMap = new Map();
  let lastResult = null;
  const docObservers = new Map();

  const formatEta = (seconds) => {
    const safe = Math.max(0, Math.round(seconds));
    const mm = String(Math.floor(safe / 60)).padStart(2, "0");
    const ss = String(safe % 60).padStart(2, "0");
    return `~${mm}:${ss}`;
  };

  const closeExportSuccessModal = () => {
    if (!exportSuccessModal) return;
    exportSuccessModal.classList.add("hidden");
    exportSuccessModal.setAttribute("aria-hidden", "true");
  };

  const openExportSuccessModal = () => {
    if (!exportSuccessModal) return;
    exportSuccessModal.classList.remove("hidden");
    exportSuccessModal.setAttribute("aria-hidden", "false");
  };

  if (exportSuccessClose) {
    exportSuccessClose.addEventListener("click", closeExportSuccessModal);
  }
  if (exportSuccessModal) {
    exportSuccessModal.addEventListener("click", (event) => {
      if (event.target === exportSuccessModal) closeExportSuccessModal();
    });
  }

  const updateProgress = () => {
    const elapsed = (Date.now() - startTs) / 1000;
    const remaining = progress > 2 ? elapsed * ((100 - progress) / progress) : 150;
    const isTop10Mode = !structureMode || structureMode.value === "top10";
    let status = isTop10Mode ? "Этап 1/4: получение top-10" : "Этап 1/4: подготовка списка сайтов";
    if (progress >= 28) status = "Этап 2/4: скриншоты и текст";
    if (progress >= 62) status = "Этап 3/4: блоки и выводы";
    if (progress >= 85) status = "Этап 4/4: предложение структуры";

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
      progressIdle.textContent = "Анализ структуры завершён.";
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
    const list = Array.isArray(errors) ? errors.filter(Boolean) : [];
    const type = options.type || "error";
    const shortText = options.short || list[0] || "";
    if (list.length > 0) {
      errorsBox.classList.remove("hidden");
      errorsBox.classList.toggle("info", type === "info");
      errorsBox.replaceChildren();
      const main = document.createElement("p");
      main.textContent = shortText;
      errorsBox.appendChild(main);
      const details = list.length > 1 || (list[0] && list[0] !== shortText);
      if (details) {
        const wrap = document.createElement("details");
        wrap.className = "technical-notes";
        const summary = document.createElement("summary");
        summary.textContent = "Показать детали";
        wrap.appendChild(summary);
        list.forEach((err) => {
          const p = document.createElement("p");
          p.className = "technical-notes-text";
          p.textContent = err;
          wrap.appendChild(p);
        });
        errorsBox.appendChild(wrap);
      }
    } else {
      errorsBox.classList.add("hidden");
      errorsBox.classList.remove("info");
      errorsBox.replaceChildren();
    }
  };

  const renderStatusNotice = (payload) => {
    const errors = payload.errors || [];
    const reason = payload.export_matrix_reason || "";
    const source = payload.structures_rows_source || "";
    const recovered = ["service_data_json_block", "fenced_json_block", "raw_json_array_scan"].includes(source);

    if (!payload.export_matrix_ready) {
      const message = reason || "Матрица не сформирована, выгружен текстовый fallback.";
      renderErrors([message, ...errors], { type: "error", short: message });
      return;
    }

    if (errors.length > 0) {
      renderErrors(errors, { type: "error" });
      return;
    }

    if (recovered) {
      const infoText = reason || "Структура восстановлена автоматически из JSON-блока.";
      renderErrors([infoText], { type: "info", short: "Структура восстановлена автоматически из JSON-блока." });
      return;
    }

    renderErrors([]);
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
    const linkTokens = [];
    html = html.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, (_m, label, href) => {
      const token = `@@MDLINK_${linkTokens.length}@@`;
      linkTokens.push({ token, html: `<a class=\"doc-link\" href=\"${href}\" target=\"_blank\" rel=\"noreferrer\" title=\"${href}\">${label}</a>` });
      return token;
    });
    html = html.replace(/(https?:\/\/[^\s<]+)/g, '<a class="doc-link" href="$1" target="_blank" rel="noreferrer" title="$1">$1</a>');
    linkTokens.forEach((item) => {
      html = html.replace(item.token, item.html);
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

  const parseRegionId = (value) => {
    const text = String(value || "").trim();
    if (!text) return "";
    if (/^\d+$/.test(text)) return text;
    const bracketMatch = text.match(/\((\d+)\)\s*$/);
    if (bracketMatch) return bracketMatch[1];
    if (regionMap.has(text)) return String(regionMap.get(text));
    return "";
  };

  const applyRegionSelection = (id, name) => {
    if (!id) return;
    regionField.value = String(id);
    if (name) {
      regionSearchField.value = `${name} (${id})`;
      if (regionHint) regionHint.textContent = `Выбран регион: ${name} (${id})`;
    } else if (regionHint) {
      regionHint.textContent = `Выбран регион: ${id}`;
    }
  };

  const renderRegionOptions = (items) => {
    if (!regionList) return;
    regionMap.clear();
    regionList.replaceChildren();
    (items || []).forEach((item) => {
      const id = String(item.id || "");
      const name = String(item.name || "");
      if (!id || !name) return;
      const option = document.createElement("option");
      option.value = `${name} (${id})`;
      regionList.appendChild(option);
      regionMap.set(option.value, id);
      regionMap.set(name, id);
    });
  };

  const fetchRegionSuggestions = async (query) => {
    try {
      const response = await fetch(`/top10-region-suggest?q=${encodeURIComponent(query || "")}`);
      const payload = await response.json();
      renderRegionOptions(payload.items || []);
    } catch (_error) {
      // no-op
    }
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

  const humanizeBlockName = (value) => {
    const text = String(value || "").trim();
    if (!text) return "";
    const mRuWithId = text.match(/^(.+?)\s*\(([a-z0-9_/-]+)\)\s*$/i);
    if (mRuWithId && /[А-Яа-яЁё]/.test(mRuWithId[1])) return mRuWithId[1].trim();
    const mIdWithRu = text.match(/^([a-z0-9_/-]+)\s*\((.+?)\)\s*$/i);
    if (mIdWithRu && /[А-Яа-яЁё]/.test(mIdWithRu[2])) return mIdWithRu[2].trim();
    if (/^[a-z0-9_/-]+$/i.test(text)) {
      const normalized = text.replace(/[\/_-]+/g, " ").trim();
      if (!normalized) return text;
      return normalized.charAt(0).toUpperCase() + normalized.slice(1);
    }
    return text;
  };

  const inferTypeFromUrl = (url) => {
    const source = String(url || "").toLowerCase();
    if (!source) return "не определён";
    if (/(blog|article|news|media|wiki|academy|guide|faq)/.test(source)) return "контентная";
    return "сервисная";
  };

  const buildLightOverviewMd = (payload) => {
    const urls = (payload.urls || []).map((item) => String(item.url || "").trim()).filter(Boolean);
    const urlList = urls.length > 0 ? urls : (payload.pages || []).map((p) => String(p.url || "").trim()).filter(Boolean);
    const urlLines = urlList.length > 0
      ? urlList.map((url, index) => `${index + 1}. ${url}`).join("\n")
      : "— список страниц не получен.";

    const rows = Array.isArray(payload.structures_rows) ? payload.structures_rows : [];
    const explicitTypes = rows
      .map((row) => String(row.page_type || "").trim().toLowerCase())
      .filter(Boolean);
    let serviceCount = explicitTypes.filter((t) => t === "service").length;
    let articleCount = explicitTypes.filter((t) => t === "portal_article").length;
    if (!explicitTypes.length && urlList.length) {
      const inferred = urlList.map((url) => inferTypeFromUrl(url));
      serviceCount = inferred.filter((t) => t === "сервисная").length;
      articleCount = inferred.filter((t) => t === "контентная").length;
    }
    const unknownCount = Math.max(0, urlList.length - serviceCount - articleCount);
    const typeLines = [
      `- Сервисные: ${serviceCount}`,
      `- Контентные/портальные: ${articleCount}`,
    ];
    if (unknownCount > 0) typeLines.push(`- Не определены: ${unknownCount}`);

    const proposedRows = Array.isArray(payload.sheet3_proposed_rows) ? payload.sheet3_proposed_rows : [];
    const blockLines = [];
    for (let i = 1; i < proposedRows.length; i += 1) {
      const row = proposedRows[i];
      if (!Array.isArray(row) || row.length < 1) continue;
      const raw = String(row[0] || "").trim();
      if (!raw) continue;
      if (/не удалось выделить структуру/i.test(raw)) continue;
      const block = humanizeBlockName(raw);
      if (!block) continue;
      if (blockLines.includes(block)) continue;
      blockLines.push(block);
      if (blockLines.length >= 14) break;
    }
    if (!blockLines.length) {
      const fallback = String(payload.structure_proposal || "")
        .split("\n")
        .map((line) => line.replace(/^\s*(?:[-*•]|\d+[.)])\s*/, "").trim())
        .filter((line) => line && !line.startsWith("#"))
        .slice(0, 10)
        .map(humanizeBlockName);
      fallback.forEach((item) => {
        if (item && !blockLines.includes(item)) blockLines.push(item);
      });
    }
    const structureLines = blockLines.length
      ? blockLines.map((block, index) => `${index + 1}. ${block}`).join("\n")
      : "1. Структура не извлечена автоматически.";

    return [
      "## Коротко по выборке",
      "",
      "Я проанализировал страницы:",
      urlLines,
      "",
      "Выявленные типы страниц:",
      ...typeLines,
      "",
      "Исходя из этого, рекомендую такую структуру страницы:",
      structureLines,
    ].join("\n");
  };

  const renderResult = (payload) => {
    lastResult = payload;
    emptyState.classList.add("hidden");
    resultView.classList.remove("hidden");
    runTitle.textContent = `Отчёт: структура по конкурентам ${payload.run_id || ""}`.trim();
    renderStatusNotice(payload);

    if (lightOverviewWrap && lightOverviewDoc && lightOverviewToc) {
      const isLight = String(payload.top10_variant || "").toLowerCase() === "light";
      if (isLight) {
        const overview = buildLightOverviewMd(payload);
        lightOverviewWrap.classList.remove("hidden");
        renderDoc(prettifyTaxonomyText(overview), lightOverviewToc, lightOverviewDoc, "Коротко", "top10-light-overview");
      } else {
        lightOverviewWrap.classList.add("hidden");
        lightOverviewDoc.innerHTML = "";
        lightOverviewToc.innerHTML = "";
      }
    }

    const urls = payload.urls || [];
    const urlsText = urls.length > 0
      ? urls.map((item, index) => `${index + 1}. [${item.url}](${item.url})${item.count ? ` — встречаемость: ${item.count}` : ""}`).join("\n")
      : "URL не получены.";

    renderDoc(prettifyTaxonomyText(urlsText), urlsToc, urlsUsedDoc, "Использованные URL", "top10-urls");
    renderDoc(prettifyTaxonomyText(payload.summary_report || ""), summaryToc, summaryDoc, "Общие выводы", "top10-summary");
    renderDoc(prettifyTaxonomyText(payload.normalized_blocks || ""), blocksToc, blocksDoc, "Нормализованные блоки", "top10-blocks");
    renderDoc(prettifyTaxonomyText(payload.structure_proposal || ""), structureToc, structureDoc, "Предложение по структуре", "top10-structure");

    selectTab("top10-panel-summary");

    pagesGrid.replaceChildren();
    (payload.pages || []).forEach((page) => pagesGrid.appendChild(createPageCard(page)));

    if (exportSheetsBtn) {
      exportSheetsBtn.disabled = !payload.export_matrix_ready;
      if (!payload.export_matrix_ready) {
        exportSheetsBtn.title = payload.export_matrix_reason || "Матрица не сформирована, экспорт будет fallback.";
      } else {
        exportSheetsBtn.title = "";
      }
    }
  };

  const syncStructureMode = () => {
    const isTop10Mode = !structureMode || structureMode.value === "top10";
    if (queriesFieldLabel) queriesFieldLabel.classList.toggle("hidden", !isTop10Mode);
    if (regionFieldLabel) regionFieldLabel.classList.toggle("hidden", !isTop10Mode);
    if (top10SourceBlock) {
      const title = top10SourceBlock.querySelector("h2");
      if (title) title.classList.toggle("hidden", !isTop10Mode);
    }
    if (showBtn) showBtn.classList.toggle("hidden", !isTop10Mode);
    if (queriesField) queriesField.required = isTop10Mode;
    if (fetchStatus && !isTop10Mode) fetchStatus.classList.add("hidden");
    if (progressIdle && !progressWrap.classList.contains("hidden")) return;
    if (progressIdle) {
      progressIdle.textContent = isTop10Mode
        ? "Запустите анализ структуры по конкурентам."
        : "Запустите анализ по списку сайтов.";
    }
  };

  const collectQueriesAndRegion = () => {
    const queries = (queriesField.value || "").replace(/\\n/g, "\n").trim();
    const parsed = parseRegionId(regionSearchField.value);
    if (parsed) {
      applyRegionSelection(parsed, regionSearchField.value.replace(/\s*\(\d+\)\s*$/, ""));
    }
    const region = (regionField.value || "225").trim();
    return { queries, region };
  };

  const scrollToUrlsList = () => {
    if (!urlsField) return;
    const targetSection = urlsField.closest(".form-section") || urlsField;
    if (!targetSection || typeof targetSection.scrollIntoView !== "function") return;
    targetSection.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  const showTop10 = async () => {
    if (structureMode && structureMode.value !== "top10") return;

    const { queries, region } = collectQueriesAndRegion();
    if (!queries) {
      renderErrors(["Добавьте хотя бы один поисковый запрос."]);
      return;
    }

    showBtn.disabled = true;
    buildBtn.disabled = true;
    renderErrors([]);
    if (fetchStatus) {
      fetchStatus.textContent = "Собираю топ-10…";
      fetchStatus.classList.remove("hidden");
    }

    try {
      const formData = new FormData();
      formData.set("search_queries", queries);
      formData.set("region_id", region || "225");
      formData.set("top_n", "10");
      const response = await fetch("/top10-urls", { method: "POST", body: formData });
      const payload = await response.json();
      renderErrors(payload.errors || []);

      if (payload.urls && payload.urls.length > 0) {
        urlsField.value = payload.urls.map((row) => row.url).join("\n");
        if (fetchStatus) fetchStatus.textContent = `Готово: найдено ${payload.urls.length} URL.`;
        scrollToUrlsList();
      } else if (fetchStatus) {
        fetchStatus.textContent = payload.errors && payload.errors.length > 0
          ? payload.errors[0]
          : "Готово: URL не найдены по этим условиям.";
      }
    } catch (error) {
      renderErrors([String(error)]);
      if (fetchStatus) fetchStatus.textContent = "Ошибка при сборке top-10.";
    } finally {
      showBtn.disabled = false;
      buildBtn.disabled = false;
    }
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

  if (regionSearchField) {
    fetchRegionSuggestions(regionSearchField.value || "");
    regionSearchField.addEventListener("input", () => {
      if (regionFetchTimer) clearTimeout(regionFetchTimer);
      regionFetchTimer = window.setTimeout(() => {
        fetchRegionSuggestions(regionSearchField.value || "");
        const parsed = parseRegionId(regionSearchField.value);
        if (parsed) applyRegionSelection(parsed, regionSearchField.value.replace(/\s*\(\d+\)\s*$/, ""));
      }, 180);
    });
    regionSearchField.addEventListener("change", () => {
      const parsed = parseRegionId(regionSearchField.value);
      if (parsed) applyRegionSelection(parsed, regionSearchField.value.replace(/\s*\(\d+\)\s*$/, ""));
    });
  }

  tabButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      selectTab(btn.dataset.target);
    });
  });

  if (showBtn) showBtn.addEventListener("click", showTop10);

  if (structureMode) {
    structureMode.addEventListener("change", syncStructureMode);
    syncStructureMode();
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const isTop10Mode = !structureMode || structureMode.value === "top10";
    const normalizedQueries = (queriesField?.value || "").replace(/\\n/g, "\n").trim();
    const normalizedUrls = (urlsField?.value || "").trim();
    if (isTop10Mode && !normalizedQueries && !normalizedUrls) {
      renderErrors(["Добавьте поисковые запросы или заполните список URL вручную."]);
      return;
    }
    if (!isTop10Mode && !normalizedUrls) {
      renderErrors(["Добавьте хотя бы один URL для анализа."]);
      return;
    }

    startProgress();
    showBtn.disabled = true;
    buildBtn.disabled = true;
    renderErrors([]);

    try {
      const formData = new FormData(form);
      if (queriesField) {
        formData.set("search_queries", normalizedQueries);
      }
      form.querySelectorAll('input[type="checkbox"][name^="enabled_"]').forEach((checkbox) => {
        const input = checkbox;
        formData.set(input.name, input.checked ? "1" : "0");
      });

      const response = await fetch("/analyze-top10-structure", {
        method: "POST",
        body: formData,
      });
      const payload = await response.json();
      renderResult(payload);
      if (!response.ok || (payload.errors && payload.errors.length > 0 && !payload.summary_report && !payload.normalized_blocks && !payload.structure_proposal)) {
        failProgress();
      } else {
        completeProgress();
      }
    } catch (error) {
      renderResult({
        run_id: "",
        urls: [],
        pages: [],
        summary_report: "",
        normalized_blocks: "",
        structure_proposal: "",
        errors: [String(error)],
      });
      failProgress();
    } finally {
      showBtn.disabled = false;
      buildBtn.disabled = false;
    }
  });

  if (exportSheetsBtn) {
    exportSheetsBtn.addEventListener("click", async () => {
      if (!lastResult || !lastResult.export_matrix_ready) return;
      exportSheetsBtn.disabled = true;
      exportSheetsBtn.textContent = "Отправляем…";
      try {
        const response = await fetch("/export/google-sheets", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            report_type: "top10",
            payload: lastResult,
          }),
        });
        const payload = await response.json();
        if (!response.ok || (payload.errors && payload.errors.length > 0)) {
          throw new Error((payload.errors || []).join("; ") || "Ошибка экспорта");
        }
        const urls = Array.isArray(payload.spreadsheet_urls) && payload.spreadsheet_urls.length > 0
          ? payload.spreadsheet_urls
          : [payload.spreadsheet_url];
        urls.forEach((url) => {
          if (url) window.open(url, "_blank", "noopener,noreferrer");
        });
        openExportSuccessModal();
        renderErrors([]);
        exportSheetsBtn.textContent = "Готово";
      } catch (error) {
        const message = String(error || "");
        if (
          message.includes("top10.v3")
          || message.includes("compare_sheet")
          || message.includes("sites_sheet")
          || message.includes("structure_sheet")
        ) {
          renderErrors(["Экспорт невозможен: обновите Apps Script для формата top10.v3 (compare/sites/structure)."]);
        } else {
          renderErrors([message || "Ошибка экспорта в Google Sheets."]);
        }
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
