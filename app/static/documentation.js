/* Навигация, копирование по HTTP и локальный рендер схем из README. */
(function () {
    const article = document.querySelector('.documentation-article');
    if (!article) return;
    const navigation = document.querySelector('.documentation-navigation');
    if (window.matchMedia('(max-width: 1199px)').matches) navigation.open = false;

    async function copyText(text) {
        if (window.isSecureContext && navigator.clipboard) {
            try {
                await navigator.clipboard.writeText(text);
                return;
            } catch (_) { /* Отказ разрешения: используем выделение в документе. */ }
        }
        const previousFocus = document.activeElement;
        const field = document.createElement('textarea');
        field.value = text;
        field.setAttribute('readonly', '');
        field.style.cssText = 'position:fixed;left:-9999px;top:0;opacity:0';
        document.body.appendChild(field);
        field.select();
        field.setSelectionRange(0, field.value.length);
        try {
            if (!document.execCommand('copy')) throw new Error('copy unavailable');
        } finally {
            field.remove();
            previousFocus?.focus({preventScroll: true});
        }
    }

    article.querySelectorAll('pre > code').forEach((code) => {
        const pre = code.parentElement;
        pre.tabIndex = 0;
        const wrapper = document.createElement('div');
        wrapper.className = 'documentation-code';
        pre.before(wrapper);
        const toolbar = document.createElement('div');
        toolbar.className = 'documentation-code-toolbar';
        const label = document.createElement('span');
        label.textContent = {bash: 'Bash', json: 'JSON', text: 'Текст', mermaid: 'Схема Mermaid'}[pre.dataset.language] || pre.dataset.language || 'Код';
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'btn btn-outline-secondary';
        button.textContent = 'Копировать';
        button.setAttribute('aria-label', 'Копировать блок: ' + label.textContent);
        button.addEventListener('click', async () => {
            let message;
            try {
                await copyText(code.textContent);
                message = 'Скопировано';
            } catch (_) {
                const selection = window.getSelection();
                const range = document.createRange();
                range.selectNodeContents(code);
                selection.removeAllRanges();
                selection.addRange(range);
                message = 'Текст выделен — нажмите Ctrl+C';
            }
            button.textContent = message;
            document.querySelector('#documentation-copy-status').textContent = message;
            window.setTimeout(() => { button.textContent = 'Копировать'; }, 2500);
        });
        toolbar.append(label, button);
        wrapper.append(toolbar, pre);
    });

    async function renderDiagrams() {
        if (!window.mermaid) return; // Исходный код остаётся читаемым без библиотеки.
        mermaid.initialize({
            startOnLoad: false, securityLevel: 'strict', theme: 'dark',
            suppressErrorRendering: true,
            flowchart: {htmlLabels: false, useMaxWidth: true},
            themeVariables: {fontFamily: 'Arial, sans-serif', primaryColor: '#252525', primaryTextColor: '#f0f0f0', primaryBorderColor: '#f5b800', lineColor: '#999999'}
        });
        let index = 0;
        for (const code of article.querySelectorAll('pre > code.language-mermaid')) {
            const block = code.closest('.documentation-code');
            try {
                const {svg} = await mermaid.render('documentation-diagram-' + index++, code.textContent);
                const diagram = document.createElement('div');
                diagram.className = 'documentation-diagram';
                diagram.setAttribute('role', 'img');
                diagram.setAttribute('aria-label', 'Схема из документации');
                diagram.tabIndex = 0;
                diagram.innerHTML = svg;
                const enlarge = document.createElement('a');
                const svgUrl = URL.createObjectURL(new Blob([svg], {type: 'image/svg+xml'}));
                enlarge.href = svgUrl;
                enlarge.target = '_blank';
                enlarge.rel = 'noopener noreferrer';
                enlarge.className = 'documentation-diagram-link';
                enlarge.textContent = 'Открыть схему крупнее ↗';
                window.addEventListener('pagehide', (event) => {
                    if (!event.persisted) URL.revokeObjectURL(svgUrl);
                });
                const source = document.createElement('details');
                source.className = 'documentation-diagram-source';
                const summary = document.createElement('summary');
                summary.textContent = 'Показать код схемы';
                block.before(enlarge, diagram, source);
                source.append(summary, block);
            } catch (_) {
                const message = document.createElement('p');
                message.className = 'text-muted';
                message.textContent = 'Не удалось построить схему. Ниже доступен её исходный код.';
                block.before(message);
            }
        }
    }
    renderDiagrams();
})();
