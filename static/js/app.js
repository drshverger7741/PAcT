function openModal(title, description, targetUrl, targetId, currentComment) {
    document.getElementById('modal-title').innerHTML = `📝 ${title}`;
    document.getElementById('modal-description').innerText = description;
    document.getElementById('modal-comment-text').value = currentComment || '';
    
    const form = document.getElementById('comment-form');
    form.setAttribute('hx-post', targetUrl);
    form.setAttribute('hx-target', '#' + targetId);
    
    // Re-process the form with HTMX to update attributes
    if (window.htmx) {
        htmx.process(form);
    }
    
    document.getElementById('comment-modal').setAttribute('open', 'true');
}

function closeModal() {
    document.getElementById('comment-modal').removeAttribute('open');
}

// Close modal on Esc key
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        closeModal();
        closeWindowStats();
    }
});

let windowChart = null;
let fullTimelineData = [];
let currentStatsParams = { title: '', start: '', end: '' };

function switchTab(tabId) {
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    
    if (event && event.target) {
        event.target.classList.add('active');
    }
    document.getElementById(`tab-${tabId}`).classList.add('active');
}

async function refreshWindowStats() {
    const { start, end } = currentStatsParams;
    if (!start) return;
    
    const response = await fetch(`/api/window_stats?start=${start}&end=${end}`);
    const data = await response.json();
    
    fullTimelineData = data.timeline || [];
    
    // Overview Table
    const tbody = document.getElementById('window-stats-table-body');
    if (tbody) {
        tbody.innerHTML = data.table.map(row => `
            <tr>
                <td><span class="app-badge" title="${row.app_name}">${row.app_name}</span></td>
                <td>
                    <div style="max-width: 0; min-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
                        <span title="${row.window_title}">${row.window_title}</span>
                    </div>
                </td>
                <td class="duration-text" style="text-align: right; white-space: nowrap;">${(row.total_duration / 3600).toFixed(2)}${CONFIG.hourShort}</td>
            </tr>
        `).join('');
    }
    
    // Timeline Table
    filterTimeline(); // Use current filter
    
    // Chart
    const chartCanvas = document.getElementById('window-stats-chart');
    if (chartCanvas) {
        const ctx = chartCanvas.getContext('2d');
        if (windowChart) windowChart.destroy();
        
        const isLight = document.documentElement.getAttribute('data-theme') === 'light';
        const labelColor = isLight ? '#666' : '#ccc';
        
        windowChart = new Chart(ctx, {
            type: 'doughnut',
            data: {
                labels: data.chart.labels,
                datasets: [{
                    data: data.chart.values,
                    backgroundColor: [
                        '#1095c1', '#4caf50', '#ffeb3b', '#f44336', '#9c27b0',
                        '#3f51b5', '#00bcd4', '#8bc34a', '#ffc107', '#ff9800'
                    ],
                    borderWidth: isLight ? 1 : 0,
                    borderColor: isLight ? '#fff' : 'transparent'
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { position: 'bottom', labels: { color: labelColor, padding: 20, font: { size: 16 } } },
                    title: { display: false }
                },
                cutout: '70%'
            }
        });
    }
}

async function openWindowStats(title, startDate, endDate) {
    currentStatsParams = { title, start: startDate, end: endDate };
    document.getElementById('window-stats-title').innerText = title;
    document.getElementById('window-stats-modal').setAttribute('open', 'true');
    
    // Reset to first tab
    const firstTab = document.querySelector('.tab-btn');
    if (firstTab) firstTab.click();
    
    const filterInput = document.getElementById('timeline-filter');
    if (filterInput) filterInput.value = '';
    
    await refreshWindowStats();
}

function renderTimeline(data) {
    const tbody = document.getElementById('window-timeline-table-body');
    if (tbody) {
        tbody.innerHTML = data.map(row => `
            <tr>
                <td>${row.date}</td>
                <td>${row.start_time}</td>
                <td>${row.end_time}</td>
                <td><span class="app-badge" title="${row.app_name}">${row.app_name}</span></td>
                <td>
                    <div style="max-width: 0; min-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
                        <span title="${row.window_title}">${row.window_title}</span>
                    </div>
                </td>
                <td class="duration-text" style="text-align: right;">${row.duration.toFixed(0)}${CONFIG.secShort}</td>
            </tr>
        `).join('');
    }
    const countSpan = document.getElementById('timeline-count');
    if (countSpan) {
        countSpan.innerText = CONFIG.totalCountTpl.replace('{count}', data.length);
    }
}

function filterTimeline() {
    const filterInput = document.getElementById('timeline-filter');
    if (!filterInput) return;
    
    const filter = filterInput.value.toLowerCase();
    const filteredData = fullTimelineData.filter(row => 
        row.app_name.toLowerCase().includes(filter) || 
        row.window_title.toLowerCase().includes(filter)
    );
    renderTimeline(filteredData);
}

function closeWindowStats() {
    document.getElementById('window-stats-modal').removeAttribute('open');
}
