import m from "mithril";

const TOKEN_KEY = "bridge_strain_token";
const USER_KEY = "bridge_strain_user";

const ROLE_NAMES = {
  surveyor: "测量员",
  leader: "班组长",
  reviewer: "复核员",
};

function verdictClass(verdict, status) {
  if (verdict === "合格") return "tag pass";
  if (verdict === "越界") return "tag fail";
  if (status === "pending" || status === "processing") return "tag wait";
  return "tag wait";
}

function displayVerdict(row) {
  if (row.verdict) return row.verdict;
  if (row.status === "pending") return "候审中";
  if (row.status === "processing") return "处理中";
  return "—";
}

function fmtTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("zh-CN", { hour12: false });
}

const state = {
  token: localStorage.getItem(TOKEN_KEY) || "",
  user: null,
  page: "readings",
  loginForm: { username: "surveyor", password: "surv123456" },
  submitForm: { span_code: "", microstrain: "" },
  applyForm: { span_code: "" },
  rows: [],
  applications: [],
  error: "",
  msg: "",
  loading: false,
  timer: null,
};

try {
  state.user = JSON.parse(localStorage.getItem(USER_KEY) || "null");
} catch {
  state.user = null;
}

function roles() {
  return (state.user && state.user.roles) || [];
}

function canSubmit() {
  return roles().includes("surveyor");
}

function canConfirm() {
  return roles().includes("leader");
}

function roleLabel() {
  const names = roles().map((r) => ROLE_NAMES[r] || r);
  return names.length ? names.join("·") : "未知";
}

function confirmedApplications() {
  return state.applications.filter((a) => a.status === "confirmed");
}

function pendingApplications() {
  return state.applications.filter((a) => a.status === "pending");
}

async function api(path, opts = {}) {
  const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  const res = await fetch(path, { ...opts, headers });
  const text = await res.text();
  let data = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    data = { detail: text };
  }
  if (!res.ok) throw new Error(data.detail || res.statusText);
  return data;
}

async function loadReadings() {
  if (!state.token) return;
  try {
    state.rows = await api("/api/readings");
    state.error = "";
  } catch {
    state.error = "加载列表失败，请重新登录";
  }
}

async function loadApplications() {
  if (!state.token) return;
  try {
    state.applications = await api("/api/span-applications");
  } catch {
    // 列表加载失败不打断页面，下轮轮询重试
  }
}

async function loadAll() {
  await loadReadings();
  await loadApplications();
  m.redraw();
}

function startPolling() {
  if (state.timer) clearInterval(state.timer);
  if (!state.token) return;
  state.timer = setInterval(loadAll, 3000);
}

function switchPage(page) {
  state.page = page;
  state.error = "";
  state.msg = "";
  m.redraw();
}

async function submitReading(e) {
  e.preventDefault();
  state.error = "";
  state.msg = "";
  state.loading = true;
  try {
    const data = await api("/api/readings", {
      method: "POST",
      body: JSON.stringify({
        span_code: state.submitForm.span_code,
        microstrain: parseFloat(state.submitForm.microstrain),
      }),
    });
    state.msg = data.message || "已提交";
    state.submitForm = { span_code: "", microstrain: "" };
    await loadReadings();
  } catch (err) {
    state.error = err.message || "提交失败";
  } finally {
    state.loading = false;
    m.redraw();
  }
}

async function applySpan(e) {
  e.preventDefault();
  state.error = "";
  state.msg = "";
  state.loading = true;
  try {
    await api("/api/span-applications", {
      method: "POST",
      body: JSON.stringify({ span_code: state.applyForm.span_code }),
    });
    state.msg = `已申请开放跨段 ${state.applyForm.span_code}，等待班组长确认`;
    state.applyForm = { span_code: "" };
    await loadApplications();
  } catch (err) {
    state.error = err.message || "申请失败";
  } finally {
    state.loading = false;
    m.redraw();
  }
}

async function confirmSpan(appId) {
  state.error = "";
  state.msg = "";
  state.loading = true;
  try {
    const data = await api(`/api/span-applications/${appId}/confirm`, {
      method: "POST",
      body: "{}",
    });
    state.msg = data.message || "已确认";
    await loadApplications();
  } catch (err) {
    state.error = err.message || "确认失败";
  } finally {
    state.loading = false;
    m.redraw();
  }
}

function loginView() {
  return m("div.wrap", [
    m("h1", "桥梁应变班交台"),
    m(
      "p.sub",
      "测量员提交跨段编号与微应变读数，跨段须经班组长确认开放后方可报送。"
    ),
    m("div.card", [
      m(
        "form",
        {
          onsubmit: async (e) => {
            e.preventDefault();
            state.error = "";
            state.loading = true;
            try {
              const data = await api("/api/auth/login", {
                method: "POST",
                body: JSON.stringify(state.loginForm),
              });
              state.token = data.access_token;
              state.user = { username: data.username, roles: data.roles || [] };
              localStorage.setItem(TOKEN_KEY, state.token);
              localStorage.setItem(USER_KEY, JSON.stringify(state.user));
              await loadAll();
              startPolling();
            } catch {
              state.error = "用户名或密码错误";
            } finally {
              state.loading = false;
              m.redraw();
            }
          },
        },
        [
          m("div.row", [
            m("label", [
              "用户名",
              m("input", {
                value: state.loginForm.username,
                oninput: (e) => {
                  state.loginForm.username = e.target.value;
                },
              }),
            ]),
            m("label", [
              "密码",
              m("input", {
                type: "password",
                value: state.loginForm.password,
                oninput: (e) => {
                  state.loginForm.password = e.target.value;
                },
              }),
            ]),
            m("button", { type: "submit", disabled: state.loading }, "登录"),
          ]),
          state.error ? m("p.err", state.error) : null,
        ]
      ),
      m(
        "p.sub",
        { style: { marginBottom: 0 } },
        "测量员 surveyor / surv123456 · 甲班组长 leader_a / lead123456 · 复核员（兼班组长） reviewer / rev123456"
      ),
    ]),
  ]);
}

function topbarView() {
  return m("div.topbar", [
    m("div", [
      m("h1", "桥梁应变班交台"),
      m("p.sub", "微应变 80～220 με 为合格，否则为越界；跨段须班组长确认开放后才可报送。"),
    ]),
    m("div.topnav", [
      m(
        "button",
        {
          type: "button",
          class: state.page === "readings" ? "" : "secondary",
          onclick: () => switchPage("readings"),
        },
        "读数台"
      ),
      m(
        "button",
        {
          type: "button",
          class: state.page === "confirm" ? "" : "secondary",
          onclick: () => switchPage("confirm"),
        },
        "班组确认"
      ),
      m(
        "span.user",
        `${state.user?.username}（${roleLabel()}）`
      ),
      m(
        "button.secondary",
        {
          type: "button",
          onclick: () => {
            localStorage.removeItem(TOKEN_KEY);
            localStorage.removeItem(USER_KEY);
            state.token = "";
            state.user = null;
            state.rows = [];
            state.applications = [];
            state.page = "readings";
            if (state.timer) clearInterval(state.timer);
            m.redraw();
          },
        },
        "退出"
      ),
    ]),
  ]);
}

function submitCardView() {
  const openSpans = confirmedApplications().map((a) => a.span_code);
  return m("div.card", [
    m("h2", { style: { marginTop: 0, fontSize: "1.1rem" } }, "提交读数"),
    m("form", { onsubmit: submitReading }, [
      m("div.row", [
        m("label", [
          "跨段编号",
          m("input", {
            required: true,
            placeholder: "仅可填写已确认开放的跨段",
            list: "open-spans",
            value: state.submitForm.span_code,
            oninput: (e) => {
              state.submitForm.span_code = e.target.value;
            },
          }),
          m(
            "datalist#open-spans",
            openSpans.map((s) => m("option", { key: s, value: s }))
          ),
        ]),
        m("label", [
          "微应变（με）",
          m("input", {
            required: true,
            type: "number",
            step: "0.1",
            value: state.submitForm.microstrain,
            oninput: (e) => {
              state.submitForm.microstrain = e.target.value;
            },
          }),
        ]),
        m("button", { type: "submit", disabled: state.loading }, "提交"),
      ]),
      m(
        "p.sub",
        { style: { margin: "0.5rem 0 0" } },
        openSpans.length
          ? `已开放跨段：${openSpans.join("、")}`
          : "暂无已开放跨段，请先在「班组确认」页申请，待班组长确认后再报送。"
      ),
      state.error ? m("p.err", state.error) : null,
      state.msg ? m("p.ok", state.msg) : null,
    ]),
  ]);
}

function readingsTableView() {
  return m("div.card", [
    m("h2", { style: { marginTop: 0, fontSize: "1.1rem" } }, "读数列表"),
    m("table", [
      m("thead", [
        m("tr", [
          m("th", "编号"),
          m("th", "跨段"),
          m("th", "微应变"),
          m("th", "结论"),
          m("th", "说明"),
          m("th", "状态"),
          m("th", "提交人"),
        ]),
      ]),
      m(
        "tbody",
        state.rows.length
          ? state.rows.map((r) =>
              m("tr", { key: r.id }, [
                m("td", r.id),
                m("td", r.span_code),
                m("td", r.microstrain),
                m("td", [
                  m(
                    "span",
                    { class: verdictClass(r.verdict, r.status) },
                    displayVerdict(r)
                  ),
                ]),
                m("td", r.reason || "—"),
                m("td", r.status),
                m("td", r.created_by),
              ])
            )
          : [m("tr", m("td", { colspan: 7 }, "暂无数据"))]
      ),
    ]),
  ]);
}

function pendingColumnView() {
  const pendings = pendingApplications();
  return m("div.card.col", [
    m("h2", { style: { marginTop: 0, fontSize: "1.1rem" } }, "待确认跨段"),
    m("form", { onsubmit: applySpan }, [
      m("div.row", [
        m("label", [
          "申请开放跨段",
          m("input", {
            required: true,
            placeholder: "例如 跨中",
            value: state.applyForm.span_code,
            oninput: (e) => {
              state.applyForm.span_code = e.target.value;
            },
          }),
        ]),
        m("button", { type: "submit", disabled: state.loading }, "申请"),
      ]),
    ]),
    state.error ? m("p.err", state.error) : null,
    state.msg ? m("p.ok", state.msg) : null,
    pendings.length
      ? m("table", [
          m("thead", [
            m("tr", [
              m("th", "跨段"),
              m("th", "申请人"),
              m("th", "申请时间"),
              m("th", "操作"),
            ]),
          ]),
          m(
            "tbody",
            pendings.map((a) => {
              const isSelf = a.applicant === state.user?.username;
              let action;
              if (canConfirm() && !isSelf) {
                action = m(
                  "button",
                  {
                    type: "button",
                    disabled: state.loading,
                    onclick: () => confirmSpan(a.id),
                  },
                  "确认"
                );
              } else if (canConfirm() && isSelf) {
                action = m("span.muted", "本人申请，不可自确");
              } else {
                action = m("span.muted", "待班组长确认");
              }
              return m("tr", { key: a.id }, [
                m("td", a.span_code),
                m("td", a.applicant),
                m("td", fmtTime(a.created_at)),
                m("td", action),
              ]);
            })
          ),
        ])
      : m("p.sub", "暂无待确认申请"),
  ]);
}

function confirmedColumnView() {
  const confirmed = confirmedApplications();
  return m("div.card.col", [
    m("h2", { style: { marginTop: 0, fontSize: "1.1rem" } }, "已确认清单"),
    confirmed.length
      ? confirmed.map((a) =>
          m("div.confirmed-item", { key: a.id }, [
            m("div.confirmed-head", [
              m("span.tag.pass", "已确认"),
              m("strong", ` ${a.span_code}`),
              m(
                "span.muted",
                ` 确认人 ${a.confirmed_by || "—"} · ${fmtTime(a.confirmed_at)}`
              ),
            ]),
            a.ledger && a.ledger.length
              ? m(
                  "ul.ledger",
                  a.ledger.map((entry) =>
                    m(
                      "li",
                      { key: entry.id },
                      `流水 #${entry.id} · ${entry.action === "confirm" ? "确认开放" : entry.action} · 申请人 ${entry.applicant} · 确认人 ${entry.confirmer} · ${fmtTime(entry.created_at)}`
                    )
                  )
                )
              : m("p.muted", "暂无确认流水"),
          ])
        )
      : m("p.sub", "暂无已确认跨段"),
  ]);
}

function confirmPageView() {
  return m("div.cols", [pendingColumnView(), confirmedColumnView()]);
}

const App = {
  oninit() {
    loadAll();
    startPolling();
  },
  onremove() {
    if (state.timer) clearInterval(state.timer);
  },
  view() {
    if (!state.token) {
      return loginView();
    }
    return m("div.wrap", [
      topbarView(),
      state.page === "readings"
        ? [canSubmit() ? submitCardView() : null, readingsTableView()]
        : confirmPageView(),
    ]);
  },
};

export default App;
