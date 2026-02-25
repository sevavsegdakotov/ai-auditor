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

  const analysisSitesDoc = document.getElementById("comp-analysis-sites-doc");
  const normalizedBlocksDoc = document.getElementById("comp-normalized-blocks-doc");
  const analysisMeaningsDoc = document.getElementById("comp-analysis-meanings-doc");
  const structureProposalDoc = document.getElementById("comp-structure-proposal-doc");

  const sitesToc = document.getElementById("comp-sites-toc");
  const blocksToc = document.getElementById("comp-blocks-toc");
  const meaningsToc = document.getElementById("comp-meanings-toc");
  const structureToc = document.getElementById("comp-structure-toc");

  const pagesGrid = document.getElementById("comp-pages-grid");
  const exportSheetsBtn = document.getElementById("comp-export-sheets-btn");

  const headerMoreToggle = document.getElementById("header-more-toggle");
  const headerMore = document.getElementById("header-more");

  const promptToggles = document.querySelectorAll(".comp-prompt-toggle");
  const promptAreas = [
    document.getElementById("comp-prompt-1"),
    document.getElementById("comp-prompt-2"),
    document.getElementById("comp-prompt-3"),
    document.getElementById("comp-prompt-4"),
  ].filter(Boolean);

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
    const remaining = progress > 2 ? elapsed * ((100 - progress) / progress) : 160;
    let status = "Этап 1/4: сбор страниц";
    if (progress >= 30) status = "Этап 2/4: скриншоты и текст";
    if (progress >= 60) status = "Этап 3/4: анализ структуры и смыслов";
    if (progress >= 85) status = "Этап 4/4: предложение по структуре";

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

    renderDoc(payload.analysis_sites || "", sitesToc, analysisSitesDoc, "Анализ структуры", "comp-sites");
    renderDoc(payload.normalized_blocks || "", blocksToc, normalizedBlocksDoc, "Нормализованные блоки", "comp-blocks");
    renderDoc(payload.analysis_meanings || "", meaningsToc, analysisMeaningsDoc, "Анализ смыслов", "comp-meanings");
    renderDoc(payload.structure_proposal || "", structureToc, structureProposalDoc, "Предложение по структуре", "comp-structure");
    selectTab("panel-sites");

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

  if (headerMoreToggle && headerMore) {
    headerMoreToggle.addEventListener("click", () => {
      const hidden = headerMore.classList.contains("hidden");
      headerMore.classList.toggle("hidden", !hidden);
      headerMoreToggle.setAttribute("aria-expanded", String(hidden));
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

      if (!response.ok || (payload.errors && payload.errors.length > 0 && !payload.analysis_sites && !payload.normalized_blocks && !payload.analysis_meanings && !payload.structure_proposal)) {
        failProgress();
      } else {
        completeProgress();
      }
    } catch (error) {
      renderResult({
        run_id: "",
        pages: [],
        analysis_sites: "",
        normalized_blocks: "",
        analysis_meanings: "",
        structure_proposal: "",
        errors: [String(error)],
      });
      failProgress();
    } finally {
      submitBtn.disabled = false;
    }
  });

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
            report_type: "competitors",
            payload: lastResult,
          }),
        });
        const payload = await response.json();
        if (!response.ok || (payload.errors && payload.errors.length > 0)) {
          throw new Error((payload.errors || []).join("; ") || "Ошибка экспорта");
        }
        window.open(payload.spreadsheet_url, "_blank", "noopener,noreferrer");
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
