// js/view/jiraDeck.js — Interactive Jira Kanban Board & Issue Inspector (View layer).
// Renders structured Jira queries, Kanban columns, issue details, live status transitions,
// and live comment composition with Zen White Glassmorphic aesthetics.

let el = null;
let currentIssues = [];
let currentIssue = null;
let currentMode = "board"; // "board" | "list" | "detail"

function build() {
  el = document.createElement("div");
  el.id = "jira-deck";
  el.className = "jira-deck hidden";
  el.innerHTML = `
    <div class="jira-glass-card">
      <div class="jira-header">
        <div class="jira-header-left">
          <div class="jira-brand">
            <span class="jira-icon">🎯</span>
            <div class="jira-titles">
              <span class="jira-title">JIRA WORKSPACE</span>
              <span class="jira-subtitle" id="jira-subtitle">Interactive Issue Board</span>
            </div>
          </div>
          <div class="jira-nav-tabs">
            <button class="jira-tab active" id="jira-tab-board">📋 Board</button>
            <button class="jira-tab" id="jira-tab-list">📑 List</button>
            <button class="jira-tab hidden" id="jira-tab-detail">🔍 Issue</button>
          </div>
        </div>

        <div class="jira-header-right">
          <div class="jira-search-box">
            <span class="jira-search-icon">🔍</span>
            <input type="text" id="jira-filter-input" placeholder="Filter issues..." />
          </div>
          <button class="jira-btn jira-btn-secondary" id="jira-btn-create" title="Create New Ticket">+ Create</button>
          <button class="jira-btn jira-btn-icon" id="jira-btn-refresh" title="Refresh">⟳</button>
          <button class="jira-btn jira-btn-icon" id="jira-close" title="Dismiss (or say 'dismiss')">✕</button>
        </div>
      </div>

      <div class="jira-body">
        <!-- Kanban Board View -->
        <div id="jira-view-board" class="jira-view">
          <div class="jira-kanban-grid">
            <div class="jira-column" data-status-group="todo">
              <div class="jira-col-header">
                <span class="jira-col-dot dot-todo"></span>
                <span class="jira-col-title">TO DO</span>
                <span class="jira-col-count" id="count-todo">0</span>
              </div>
              <div class="jira-card-list" id="col-todo"></div>
            </div>

            <div class="jira-column" data-status-group="inprogress">
              <div class="jira-col-header">
                <span class="jira-col-dot dot-inprogress"></span>
                <span class="jira-col-title">IN PROGRESS</span>
                <span class="jira-col-count" id="count-inprogress">0</span>
              </div>
              <div class="jira-card-list" id="col-inprogress"></div>
            </div>

            <div class="jira-column" data-status-group="review">
              <div class="jira-col-header">
                <span class="jira-col-dot dot-review"></span>
                <span class="jira-col-title">UNDER REVIEW</span>
                <span class="jira-col-count" id="count-review">0</span>
              </div>
              <div class="jira-card-list" id="col-review"></div>
            </div>

            <div class="jira-column" data-status-group="done">
              <div class="jira-col-header">
                <span class="jira-col-dot dot-done"></span>
                <span class="jira-col-title">DONE</span>
                <span class="jira-col-count" id="count-done">0</span>
              </div>
              <div class="jira-card-list" id="col-done"></div>
            </div>
          </div>
        </div>

        <!-- List View -->
        <div id="jira-view-list" class="jira-view hidden">
          <div class="jira-table-wrap">
            <table class="jira-table">
              <thead>
                <tr>
                  <th>Key</th>
                  <th>Type</th>
                  <th>Summary</th>
                  <th>Status</th>
                  <th>Priority</th>
                  <th>Assignee</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody id="jira-table-body"></tbody>
            </table>
          </div>
        </div>

        <!-- Issue Detail View -->
        <div id="jira-view-detail" class="jira-view hidden">
          <div class="jira-detail-container" id="jira-detail-content">
            <!-- Populated dynamically -->
          </div>
        </div>

        <!-- Create Issue Drawer / Form -->
        <div id="jira-view-create" class="jira-view hidden">
          <div class="jira-create-form">
            <h3>Create New Jira Issue</h3>
            <div class="jira-form-row">
              <label>Project Key</label>
              <input type="text" id="jira-new-project" placeholder="e.g. PROJ, DEV, ENG" />
            </div>
            <div class="jira-form-row">
              <label>Issue Type</label>
              <select id="jira-new-type">
                <option value="Task">Task</option>
                <option value="Bug">Bug</option>
                <option value="Story">Story</option>
                <option value="Epic">Epic</option>
              </select>
            </div>
            <div class="jira-form-row">
              <label>Summary / Title</label>
              <input type="text" id="jira-new-summary" placeholder="Short description of the task or bug" />
            </div>
            <div class="jira-form-row">
              <label>Description</label>
              <textarea id="jira-new-description" rows="5" placeholder="Detailed steps, acceptance criteria, or context..."></textarea>
            </div>
            <div class="jira-form-row">
              <label>Priority</label>
              <select id="jira-new-priority">
                <option value="Medium">Medium</option>
                <option value="High">High</option>
                <option value="Highest">Highest</option>
                <option value="Low">Low</option>
                <option value="Lowest">Lowest</option>
              </select>
            </div>
            <div class="jira-form-actions">
              <button class="jira-btn jira-btn-primary" id="jira-submit-create">Create Issue</button>
              <button class="jira-btn jira-btn-secondary" id="jira-cancel-create">Cancel</button>
            </div>
          </div>
        </div>
      </div>
    </div>`;

  document.body.appendChild(el);

  // Event Listeners
  el.querySelector("#jira-close").addEventListener("click", closeJiraDeck);
  el.querySelector("#jira-tab-board").addEventListener("click", () => switchView("board"));
  el.querySelector("#jira-tab-list").addEventListener("click", () => switchView("list"));
  el.querySelector("#jira-tab-detail").addEventListener("click", () => switchView("detail"));

  el.querySelector("#jira-filter-input").addEventListener("input", (e) => {
    const filter = e.target.value.toLowerCase().trim();
    renderBoard(currentIssues.filter(i => 
      (i.summary || "").toLowerCase().includes(filter) ||
      (i.key || "").toLowerCase().includes(filter) ||
      (i.assignee || "").toLowerCase().includes(filter)
    ));
  });

  el.querySelector("#jira-btn-create").addEventListener("click", () => switchView("create"));
  el.querySelector("#jira-cancel-create").addEventListener("click", () => switchView("board"));
  el.querySelector("#jira-submit-create").addEventListener("click", handleCreateSubmit);
  el.querySelector("#jira-btn-refresh").addEventListener("click", refreshCurrentIssues);
}

function switchView(mode) {
  currentMode = mode;
  const boardView = el.querySelector("#jira-view-board");
  const listView = el.querySelector("#jira-view-list");
  const detailView = el.querySelector("#jira-view-detail");
  const createView = el.querySelector("#jira-view-create");

  const tabBoard = el.querySelector("#jira-tab-board");
  const tabList = el.querySelector("#jira-tab-list");
  const tabDetail = el.querySelector("#jira-tab-detail");

  boardView.classList.toggle("hidden", mode !== "board");
  listView.classList.toggle("hidden", mode !== "list");
  detailView.classList.toggle("hidden", mode !== "detail");
  createView.classList.toggle("hidden", mode !== "create");

  tabBoard.classList.toggle("active", mode === "board");
  tabList.classList.toggle("active", mode === "list");
  tabDetail.classList.toggle("active", mode === "detail");
}

function getStatusGroup(statusName) {
  const s = (statusName || "").toLowerCase();
  if (s.includes("done") || s.includes("closed") || s.includes("resolved") || s.includes("complete")) return "done";
  if (s.includes("review") || s.includes("qa") || s.includes("testing") || s.includes("test")) return "review";
  if (s.includes("progress") || s.includes("doing") || s.includes("dev") || s.includes("active")) return "inprogress";
  return "todo";
}

function getTypeIcon(typeName) {
  const t = (typeName || "").toLowerCase();
  if (t.includes("bug")) return "🐞";
  if (t.includes("story")) return "📖";
  if (t.includes("epic")) return "⚡";
  if (t.includes("subtask")) return "↳";
  return "📋";
}

function getPriorityBadge(priorityName) {
  const p = (priorityName || "Medium").toLowerCase();
  let color = "#eab308";
  if (p.includes("highest") || p.includes("critical") || p.includes("blocker")) color = "#ef4444";
  else if (p.includes("high")) color = "#f97316";
  else if (p.includes("low")) color = "#10b981";
  else if (p.includes("lowest")) color = "#64748b";
  return `<span class="jira-priority-pill" style="--p-color: ${color}">${priorityName || 'Normal'}</span>`;
}

export function openJiraDeck() {
  if (!el) build();
  el.classList.remove("hidden");
}

export function closeJiraDeck() {
  if (!el) return;
  el.classList.add("hidden");
}

export function isJiraDeckOpen() {
  return !!el && !el.classList.contains("hidden");
}

export function renderJiraData(jiraObj) {
  if (!el) build();
  openJiraDeck();

  if (!jiraObj) return;

  const tool = jiraObj.tool;
  const data = jiraObj.data || {};

  if (tool === "jira_search_issues") {
    currentIssues = data.issues || [];
    el.querySelector("#jira-subtitle").textContent = `${currentIssues.length} issues found`;
    renderBoard(currentIssues);
    renderList(currentIssues);
    switchView("board");
  } else if (tool === "jira_get_issue") {
    currentIssue = data;
    renderIssueDetail(data);
    el.querySelector("#jira-tab-detail").classList.remove("hidden");
    switchView("detail");
  } else if (tool === "jira_create_issue") {
    if (data.key) {
      el.querySelector("#jira-subtitle").textContent = `Created ticket ${data.key}`;
      fetchIssueDetails(data.key);
    }
  } else if (tool === "jira_transition_issue") {
    el.querySelector("#jira-subtitle").textContent = `Status updated for ${data.issue_key}`;
    if (data.issue_key) fetchIssueDetails(data.issue_key);
  }
}

function renderBoard(issues) {
  const colTodo = el.querySelector("#col-todo");
  const colInProgress = el.querySelector("#col-inprogress");
  const colReview = el.querySelector("#col-review");
  const colDone = el.querySelector("#col-done");

  colTodo.innerHTML = "";
  colInProgress.innerHTML = "";
  colReview.innerHTML = "";
  colDone.innerHTML = "";

  const counts = { todo: 0, inprogress: 0, review: 0, done: 0 };

  issues.forEach(issue => {
    const group = getStatusGroup(issue.status);
    counts[group]++;

    const card = document.createElement("div");
    card.className = "jira-card";
    card.innerHTML = `
      <div class="jira-card-top">
        <span class="jira-card-type">${getTypeIcon(issue.type)}</span>
        <span class="jira-card-key">${issue.key}</span>
        ${getPriorityBadge(issue.priority)}
      </div>
      <div class="jira-card-title">${escapeHtml(issue.summary || "Untitled")}</div>
      <div class="jira-card-footer">
        <div class="jira-card-assignee">
          <span class="jira-avatar">${(issue.assignee || "?")[0].toUpperCase()}</span>
          <span class="jira-name">${escapeHtml(issue.assignee || "Unassigned")}</span>
        </div>
        <span class="jira-status-tag">${escapeHtml(issue.status || "To Do")}</span>
      </div>
    `;

    card.addEventListener("click", () => fetchIssueDetails(issue.key));

    if (group === "done") colDone.appendChild(card);
    else if (group === "review") colReview.appendChild(card);
    else if (group === "inprogress") colInProgress.appendChild(card);
    else colTodo.appendChild(card);
  });

  el.querySelector("#count-todo").textContent = counts.todo;
  el.querySelector("#count-inprogress").textContent = counts.inprogress;
  el.querySelector("#count-review").textContent = counts.review;
  el.querySelector("#count-done").textContent = counts.done;
}

function renderList(issues) {
  const tbody = el.querySelector("#jira-table-body");
  tbody.innerHTML = "";

  issues.forEach(issue => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><span class="jira-table-key">${issue.key}</span></td>
      <td>${getTypeIcon(issue.type)} ${escapeHtml(issue.type || "Task")}</td>
      <td class="jira-table-summary">${escapeHtml(issue.summary || "")}</td>
      <td><span class="jira-status-tag">${escapeHtml(issue.status || "")}</span></td>
      <td>${getPriorityBadge(issue.priority)}</td>
      <td>${escapeHtml(issue.assignee || "Unassigned")}</td>
      <td><button class="jira-btn jira-btn-sm" data-key="${issue.key}">View</button></td>
    `;
    tr.querySelector("button").addEventListener("click", () => fetchIssueDetails(issue.key));
    tbody.appendChild(tr);
  });
}

function renderIssueDetail(issue) {
  const container = el.querySelector("#jira-detail-content");
  currentIssue = issue;

  container.innerHTML = `
    <div class="jira-detail-header">
      <button class="jira-btn jira-btn-secondary" id="jira-detail-back">← Back to Board</button>
      <div class="jira-detail-actions">
        <a class="jira-btn jira-btn-secondary" href="${issue.url}" target="_blank" rel="noopener">Open in Jira ↗</a>
      </div>
    </div>

    <div class="jira-detail-main">
      <div class="jira-detail-left">
        <div class="jira-detail-key-row">
          <span class="jira-badge-type">${getTypeIcon(issue.type)} ${escapeHtml(issue.type || "Task")}</span>
          <span class="jira-badge-key">${issue.key}</span>
        </div>
        <h2 class="jira-detail-title">${escapeHtml(issue.summary || "")}</h2>

        <div class="jira-section">
          <h4>Description</h4>
          <div class="jira-description-box">
            ${issue.description ? nl2br(escapeHtml(issue.description)) : '<em class="jira-muted">No description provided.</em>'}
          </div>
        </div>

        <div class="jira-section">
          <h4>Activity & Comments</h4>
          <div class="jira-comments-thread" id="jira-comments-list">
            ${(issue.comments || []).map(c => `
              <div class="jira-comment-card">
                <div class="jira-comment-header">
                  <strong>${escapeHtml(c.author || "User")}</strong>
                  <span class="jira-muted">${escapeHtml(c.created ? c.created.slice(0, 10) : "")}</span>
                </div>
                <div class="jira-comment-body">${nl2br(escapeHtml(c.body || ""))}</div>
              </div>
            `).join("") || '<p class="jira-muted">No comments yet.</p>'}
          </div>

          <div class="jira-add-comment-box">
            <textarea id="jira-new-comment-text" rows="3" placeholder="Add a comment to this ticket..."></textarea>
            <button class="jira-btn jira-btn-primary" id="jira-btn-post-comment">Post Comment</button>
          </div>
        </div>
      </div>

      <div class="jira-detail-right">
        <div class="jira-meta-card">
          <h4>Status Workflow</h4>
          <div class="jira-status-control">
            <span class="jira-current-status">${escapeHtml(issue.status || "To Do")}</span>
            <div class="jira-transition-buttons" id="jira-transition-btns">
              <button class="jira-btn jira-btn-sm" data-trans="In Progress">▶ In Progress</button>
              <button class="jira-btn jira-btn-sm" data-trans="Done">✓ Done</button>
              <button class="jira-btn jira-btn-sm" data-trans="To Do">↺ To Do</button>
            </div>
          </div>

          <div class="jira-meta-grid">
            <div class="jira-meta-item">
              <span class="jira-meta-label">Priority</span>
              <span>${getPriorityBadge(issue.priority)}</span>
            </div>
            <div class="jira-meta-item">
              <span class="jira-meta-label">Assignee</span>
              <span>${escapeHtml(issue.assignee || "Unassigned")}</span>
            </div>
            <div class="jira-meta-item">
              <span class="jira-meta-label">Reporter</span>
              <span>${escapeHtml(issue.reporter || "Unknown")}</span>
            </div>
            <div class="jira-meta-item">
              <span class="jira-meta-label">Updated</span>
              <span class="jira-muted">${escapeHtml(issue.updated ? issue.updated.slice(0, 10) : "-")}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  `;

  container.querySelector("#jira-detail-back").addEventListener("click", () => switchView("board"));
  container.querySelector("#jira-btn-post-comment").addEventListener("click", handleAddComment);

  container.querySelectorAll("#jira-transition-btns button").forEach(btn => {
    btn.addEventListener("click", () => handleStatusTransition(issue.key, btn.getAttribute("data-trans")));
  });
}

async function fetchIssueDetails(issueKey) {
  try {
    const res = await fetch("/jira/action", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "jira_get_issue", payload: { issue_key: issueKey } })
    });
    const data = await res.json();
    if (data && !data.error) {
      renderIssueDetail(data);
      el.querySelector("#jira-tab-detail").classList.remove("hidden");
      switchView("detail");
    }
  } catch (e) {
    console.error("Failed to fetch issue details:", e);
  }
}

async function handleStatusTransition(issueKey, transitionName) {
  try {
    const res = await fetch("/jira/action", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        action: "jira_transition_issue",
        payload: { issue_key: issueKey, transition_name: transitionName }
      })
    });
    const data = await res.json();
    if (data && !data.error) {
      fetchIssueDetails(issueKey);
      refreshCurrentIssues();
    }
  } catch (e) {
    console.error("Failed to transition status:", e);
  }
}

async function handleAddComment() {
  const txt = el.querySelector("#jira-new-comment-text").value.trim();
  if (!txt || !currentIssue) return;
  el.querySelector("#jira-new-comment-text").value = "";

  try {
    const res = await fetch("/jira/action", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        action: "jira_add_comment",
        payload: { issue_key: currentIssue.key, comment: txt }
      })
    });
    const data = await res.json();
    if (data && !data.error) {
      fetchIssueDetails(currentIssue.key);
    }
  } catch (e) {
    console.error("Failed to post comment:", e);
  }
}

async function handleCreateSubmit() {
  const proj = el.querySelector("#jira-new-project").value.trim();
  const summary = el.querySelector("#jira-new-summary").value.trim();
  const type = el.querySelector("#jira-new-type").value;
  const desc = el.querySelector("#jira-new-description").value.trim();
  const priority = el.querySelector("#jira-new-priority").value;

  if (!proj || !summary) {
    alert("Project key and Summary are required.");
    return;
  }

  try {
    const res = await fetch("/jira/action", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        action: "jira_create_issue",
        payload: {
          project_key: proj,
          summary: summary,
          issue_type: type,
          description: desc,
          priority: priority
        }
      })
    });
    const data = await res.json();
    if (data && !data.error) {
      switchView("board");
      refreshCurrentIssues();
    } else {
      alert(data.error || "Failed to create issue");
    }
  } catch (e) {
    console.error("Failed to create issue:", e);
  }
}

async function refreshCurrentIssues() {
  try {
    const res = await fetch("/jira/action", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "jira_search_issues", payload: { max_results: 30 } })
    });
    const data = await res.json();
    if (data && data.issues) {
      currentIssues = data.issues;
      renderBoard(currentIssues);
      renderList(currentIssues);
    }
  } catch (e) {
    console.error("Failed to refresh issues:", e);
  }
}

function escapeHtml(str) {
  return String(str || "").replace(/[&<>"']/g, m => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;"
  })[m]);
}

function nl2br(str) {
  return escapeHtml(str).replace(/\\n/g, "<br>");
}
