(() => {
  const form = document.getElementById("top10-form");
  const queriesField = document.getElementById("top10-queries");
  const regionSearchField = document.getElementById("top10-region-search");
  const regionField = document.getElementById("top10-region-id");
  const regionList = document.getElementById("top10-region-list");
  const regionHint = document.getElementById("top10-region-hint");
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
  const urlsUsedPre = document.getElementById("top10-urls-used");
  const analysisPre = document.getElementById("top10-analysis-structure");
  const structurePre = document.getElementById("top10-structure-proposal");
  const pagesGrid = document.getElementById("top10-pages-grid");
  const exportSheetsBtn = document.getElementById("top10-export-sheets-btn");

  const headerMoreToggle = document.getElementById("header-more-toggle");
  const headerMore = document.getElementById("header-more");
  const promptToggles = document.querySelectorAll(".top10-prompt-toggle");
  const promptAreas = [
    document.getElementById("top10-prompt-1"),
    document.getElementById("top10-prompt-2"),
    document.getElementById("top10-table-blocks-prompt"),
    document.getElementById("top10-table-structure-prompt"),
  ].filter(Boolean);
  const tabButtons = document.querySelectorAll(".top10-tab-btn");
  const tabPanels = document.querySelectorAll("#top10-panel-analysis, #top10-panel-structure");

  let progressTimer = null;
  let progress = 0;
  let startTs = 0;
  let lastEtaSeconds = null;
  let etaBecameWorse = false;
  let regionFetchTimer = null;
  const regionMap = new Map();
  let lastResult = null;

  const formatEta = (seconds) => {
    const safe = Math.max(0, Math.round(seconds));
    const mm = String(Math.floor(safe / 60)).padStart(2, "0");
    const ss = String(safe % 60).padStart(2, "0");
    return `~${mm}:${ss}`;
  };

  const updateProgress = () => {
    const elapsed = (Date.now() - startTs) / 1000;
    const remaining = progress > 2 ? elapsed * ((100 - progress) / progress) : 150;
    let status = "Этап 1/4: получение top-10";
    if (progress >= 28) status = "Этап 2/4: скриншоты и текст";
    if (progress >= 62) status = "Этап 3/4: анализ структуры";
    if (progress >= 85) status = "Этап 4/4: предложение по структуре";

    progressBar.style.width = `${progress}%`;
    progressPercent.textContent = `${Math.round(progress)}%`;
    progressStatus.textContent = `${status}…`;
    if (lastEtaSeconds !== null && remaining > lastEtaSeconds + 1) {
      etaBecameWorse = true;
    }
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
      progressIdle.textContent = "Анализ top-10 завершён.";
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

  const renderErrors = (errors) => {
    if (errors && errors.length > 0) {
      errorsBox.classList.remove("hidden");
      errorsBox.replaceChildren();
      errors.forEach((err) => {
        const p = document.createElement("p");
        p.textContent = err;
        errorsBox.appendChild(p);
      });
    } else {
      errorsBox.classList.add("hidden");
      errorsBox.replaceChildren();
    }
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
      // Молча игнорируем: поле остаётся редактируемым вручную.
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

  const renderResult = (payload) => {
    lastResult = payload;
    emptyState.classList.add("hidden");
    resultView.classList.remove("hidden");
    runTitle.textContent = `Отчёт по top-10 ${payload.run_id || ""}`.trim();
    renderErrors(payload.errors || []);

    const urls = payload.urls || [];
    urlsUsedPre.textContent = urls.length > 0
      ? urls.map((item, index) => `${index + 1}. ${item.url}${item.count ? ` (встречаемость: ${item.count})` : ""}`).join("\n")
      : "";

    analysisPre.textContent = payload.analysis_structure || "";
    structurePre.textContent = payload.structure_proposal || "";
    selectTab("top10-panel-analysis");

    pagesGrid.replaceChildren();
    (payload.pages || []).forEach((page) => pagesGrid.appendChild(createPageCard(page)));
  };

  const collectQueriesAndRegion = () => {
    const queries = (queriesField.value || "").trim();
    const parsed = parseRegionId(regionSearchField.value);
    if (parsed) {
      applyRegionSelection(parsed, regionSearchField.value.replace(/\s*\(\d+\)\s*$/, ""));
    }
    const region = (regionField.value || "225").trim();
    return { queries, region };
  };

  const showTop10 = async () => {
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
      const response = await fetch("/top10-urls", {
        method: "POST",
        body: formData,
      });
      const payload = await response.json();
      renderErrors(payload.errors || []);
      if (payload.urls && payload.urls.length > 0) {
        urlsField.value = payload.urls.map((row) => row.url).join("\n");
        if (fetchStatus) fetchStatus.textContent = `Готово: найдено ${payload.urls.length} URL.`;
      } else if (fetchStatus) {
        if (payload.errors && payload.errors.length > 0) {
          fetchStatus.textContent = payload.errors[0];
        } else {
          fetchStatus.textContent = "Готово: URL не найдены по этим условиям.";
        }
      }
    } catch (error) {
      renderErrors([String(error)]);
      if (fetchStatus) fetchStatus.textContent = "Ошибка при сборке top-10.";
    } finally {
      showBtn.disabled = false;
      buildBtn.disabled = false;
    }
  };

  if (headerMoreToggle && headerMore) {
    headerMoreToggle.addEventListener("click", () => {
      const hidden = headerMore.classList.contains("hidden");
      headerMore.classList.toggle("hidden", !hidden);
      headerMoreToggle.setAttribute("aria-expanded", String(hidden));
    });
  }

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

  promptToggles.forEach((btn) => {
    btn.addEventListener("click", () => {
      const target = document.getElementById(btn.dataset.target || "");
      if (!target) return;
      const hidden = target.classList.contains("hidden");
      promptAreas.forEach((area) => {
        if (area.id !== target.id) area.classList.add("hidden");
      });
      promptToggles.forEach((b) => {
        if (b !== btn) b.textContent = "Редактировать";
      });
      target.classList.toggle("hidden", !hidden);
      btn.textContent = hidden ? "Свернуть" : "Редактировать";
    });
  });

  showBtn.addEventListener("click", showTop10);

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    startProgress();
    showBtn.disabled = true;
    buildBtn.disabled = true;
    renderErrors([]);

    try {
      const formData = new FormData(form);
      const response = await fetch("/analyze-top10-structure", {
        method: "POST",
        body: formData,
      });
      const payload = await response.json();
      renderResult(payload);
      if (!response.ok || (payload.errors && payload.errors.length > 0 && !payload.analysis_structure && !payload.structure_proposal)) {
        failProgress();
      } else {
        completeProgress();
      }
    } catch (error) {
      renderResult({
        run_id: "",
        urls: [],
        pages: [],
        analysis_structure: "",
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
      if (!lastResult) return;
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
        exportSheetsBtn.textContent = "Готово";
      } catch (_error) {
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
