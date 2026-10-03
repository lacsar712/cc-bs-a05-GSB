import m from "mithril";

const TOKEN_KEY = "bridge_strain_token";
const USER_KEY = "bridge_strain_user";
const ROLE_LABELS = { writer: "测量员", reader: "复核员", leader: "班组长" };

function roleLabel(role) {
  return ROLE_LABELS[role] || role;
}

function hasRole(role) {
  return !!state.user && state.user.roles?.includes(role);
}

function verdictClass(verdict, status) {
  if (verdict === "合格") return "tag pass";
  if (verdict === "越界") return "tag fail";
  return "tag wait";
}

function displayVerdict(row) {
  if (row.verdict) return row.verdict;
  if (row.status === "pending") return "待处理";
  if (row.status === "processing") return "处理中";
  return "—";
}

function fmtTime(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString("zh-CN", { hour12: false });
  } catch {
    return iso;
  }
}

const state = {
  token: localStorage.getItem(TOKEN_KEY) || "",
  user: null,
  loginForm: { username: "surveyor", password: "surv123456" },
  submitForm: { span_code: "", microstrain: "" },
  rows: [],
  page: "readings",
  applyForm: { span_code: "" },
  spanData: { pending: [], confirmed: [] },
  fullLog: [],
  confirmingId: null,
  error: "",
  msg: "",
  applyError: "",
  applyMsg: "",
  loading: false,
  timer: null,
};

try {
  state.user = JSON.parse(localStorage.getItem(USER_KEY) || "null");
} catch {
  state.user = null;
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
  m.redraw();
}

async function loadSpanData() {
  if (!state.token || state.page !== "confirm") return;
  try {
    state.spanData = await api("/api/span-requests");
    state.fullLog = await api("/api/span-confirmation-log");
  } catch {
    /* 保留上次数据，下个轮询周期重试 */
  }
  m.redraw();
}

function startPolling() {
  if (state.timer) clearInterval(state.timer);
  if (!state.token) return;
  state.timer = setInterval(() => {
    loadReadings();
    loadSpanData();
  }, 3000);
}

function gotoPage(page) {
  state.page = page;
  if (page === "confirm") loadSpanData();
}

function logout() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
  state.token = "";
  state.user = null;
  state.rows = [];
  state.spanData = { pending: [], confirmed: [] };
  state.fullLog = [];
  state.page = "readings";
  if (state.timer) clearInterval(state.timer);
}

const LoginPage = {
  view() {
    return m("div.wrap", [
      m("h1", "桥梁应变班交台"),
      m(
        "p.sub",
        "测量员申请开放跨段，班组长确认后方可报送读数；后台工人认领队列后判定合格或越界。"
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
                state.user = {
                  username: data.username,
                  roles: data.roles || [],
                };
                localStorage.setItem(TOKEN_KEY, state.token);
                localStorage.setItem(USER_KEY, JSON.stringify(state.user));
                await loadReadings();
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
          "测量员 surveyor / surv123456 · 复核员（兼班组长）reviewer / rev123456 · 班组长 foreman / lead123456"
        ),
      ]),
    ]);
  },
};

function topbar() {
  return m("div.topbar", [
    m("div", [
      m("h1", "桥梁应变班交台"),
      m("p.sub", "微应变 80～220 με 为合格，否则为越界。跨段须班组长确认开放后方可报送。"),
    ]),
    m("div.text-right", [
      m(
        "div.userline",
        `${state.user.username}（${state.user.roles.map(roleLabel).join("、")}）`
      ),
      m(
        "button.secondary.small",
        {
          type: "button",
          onclick: () => {
            logout();
          },
        },
        "退出"
      ),
    ]),
  ]);
}

function tabs() {
  const tab = (page, label) =>
    m(
      "button.tab" + (state.page === page ? ".active" : ""),
      { type: "button", onclick: () => gotoPage(page) },
      label
    );
  return m("div.tabs", [tab("readings", "读数报送"), tab("confirm", "班组确认")]);
}

const ReadingsPage = {
  view() {
    const isWriter = hasRole("writer");
    return [
      isWriter
        ? m("div.card", [
            m("h2", { style: { marginTop: 0, fontSize: "1.1rem" } }, "提交读数"),
            m(
              "form",
              {
                onsubmit: async (e) => {
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
                },
              },
              [
                m("div.row", [
                  m("label", [
                    "跨段编号",
                    m("input", {
                      required: true,
                      placeholder: "例如 跨中S3",
                      value: state.submitForm.span_code,
                      oninput: (e) => {
                        state.submitForm.span_code = e.target.value;
                      },
                    }),
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
                  m("button", { type: "submit", disabled: state.loading }, "报送"),
                ]),
                state.error
                  ? m("p.err", [
                      state.error,
                      m(
                        "button.link",
                        {
                          type: "button",
                          onclick: () => gotoPage("confirm"),
                        },
                        "去班组确认页申请开放 →"
                      ),
                    ])
                  : null,
                state.msg ? m("p.ok", state.msg) : null,
              ]
            ),
          ])
        : m("div.card", [
            m(
              "p.note",
              { style: { margin: 0 } },
              "当前账号无测量员角色，只读查看读数；兼班组长者可在“班组确认”页确认开放申请。"
            ),
          ]),
      m("div.card", [
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
                      m("span", { class: verdictClass(r.verdict, r.status) }, displayVerdict(r)),
                    ]),
                    m("td", r.reason || "—"),
                    m("td", r.status),
                    m("td", r.created_by),
                  ])
                )
              : [m("tr", m("td", { colspan: 7 }, "暂无数据"))]
          ),
        ]),
      ]),
    ];
  },
};

function pendingItem(item) {
  const canConfirm =
    state.spanData.can_confirm && item.requested_by !== state.user.username;
  return m("div.item", { key: item.id }, [
    m("div.item-main", [
      m("strong.item-title", item.span_code),
      m("div.meta", [
        `申请人：${item.requested_by}`,
        m("br"),
        `申请时间：${fmtTime(item.requested_at)}`,
      ]),
    ]),
    canConfirm
      ? m(
          "button.small",
          {
            type: "button",
            disabled: state.confirmingId === item.id,
            onclick: async () => {
              state.confirmingId = item.id;
              state.applyError = "";
              try {
                await api(`/api/span-requests/${item.id}/confirm`, { method: "POST" });
                await loadSpanData();
                await loadReadings();
              } catch (err) {
                state.applyError = err.message || "确认失败";
                m.redraw();
              } finally {
                state.confirmingId = null;
              }
            },
          },
          "确认开放"
        )
      : m(
          "p.note",
          { style: { margin: 0 } },
          state.spanData.can_confirm
            ? "申请人不得自我确认"
            : "等待班组长确认"
        ),
  ]);
}

function confirmedItem(item) {
  return m("div.item.done", { key: item.id }, [
    m("div.item-main", [
      m("strong.item-title", [item.span_code, m("span.tag.pass", "已开放")]),
      m("div.meta", [
        `申请人：${item.requested_by}`,
        m("br"),
        `确认人：${item.confirmed_by || "—"}`,
        m("br"),
        `确认时间：${fmtTime(item.confirmed_at)}`,
      ]),
    ]),
    m("div.loglist", [
      m("div.loghead", "确认流水"),
      (item.log || []).length
        ? item.log.map((l) =>
            m(
              "div.logline",
              { key: l.id },
              `#${l.id} ${l.action === "confirm" ? "确认" : l.action} · ${l.confirmed_by} · ${fmtTime(l.confirmed_at)}`
            )
          )
        : m("p.note", { style: { margin: 0 } }, "无流水记录"),
    ]),
  ]);
}

const ConfirmPage = {
  oninit() {
    loadSpanData();
  },
  view() {
    const canApply = state.spanData.can_apply ?? hasRole("writer");
    return [
      canApply
        ? m("div.card", [
            m("h2", { style: { marginTop: 0, fontSize: "1.1rem" } }, "申请开放跨段"),
            m(
              "form",
              {
                onsubmit: async (e) => {
                  e.preventDefault();
                  state.applyError = "";
                  state.applyMsg = "";
                  try {
                    const data = await api("/api/span-requests", {
                      method: "POST",
                      body: JSON.stringify({ span_code: state.applyForm.span_code }),
                    });
                    state.applyMsg = `跨段 ${data.span_code} 已申请，等待班组长确认`;
                    state.applyForm.span_code = "";
                    await loadSpanData();
                  } catch (err) {
                    state.applyError = err.message || "申请失败";
                  } finally {
                    m.redraw();
                  }
                },
              },
              [
                m("div.row", [
                  m("label", [
                    "跨段编号",
                    m("input", {
                      required: true,
                      placeholder: "例如 跨中甲",
                      value: state.applyForm.span_code,
                      oninput: (e) => {
                        state.applyForm.span_code = e.target.value;
                      },
                    }),
                  ]),
                  m("button", { type: "submit" }, "申请开放"),
                ]),
                state.applyError ? m("p.err", state.applyError) : null,
                state.applyMsg ? m("p.ok", state.applyMsg) : null,
              ]
            ),
          ])
        : null,
      m("div.columns", [
        m("div.card.col", [
          m("h2", { style: { marginTop: 0, fontSize: "1.05rem" } }, [
            "待确认跨段",
            m("span.count", `（${state.spanData.pending.length}）`),
          ]),
          state.spanData.pending.length
            ? state.spanData.pending.map(pendingItem)
            : m("p.note", "暂无待确认跨段"),
        ]),
        m("div.card.col", [
          m("h2", { style: { marginTop: 0, fontSize: "1.05rem" } }, [
            "已确认清单",
            m("span.count", `（${state.spanData.confirmed.length}）`),
          ]),
          state.spanData.confirmed.length
            ? state.spanData.confirmed.map(confirmedItem)
            : m("p.note", "暂无已确认跨段"),
          m("div.loglist", { style: { marginTop: "1rem" } }, [
            m("div.loghead", "全部确认流水"),
            state.fullLog.length
              ? state.fullLog.map((l) =>
                  m(
                    "div.logline",
                    { key: l.id },
                    `#${l.id} ${l.span_code} · 申请人 ${l.requested_by} → ${
                      l.action === "confirm" ? "确认" : l.action
                    }人 ${l.confirmed_by} · ${fmtTime(l.confirmed_at)}`
                  )
                )
              : m("p.note", { style: { margin: 0 } }, "暂无流水"),
          ]),
        ]),
      ]),
    ];
  },
};

const App = {
  oninit() {
    loadReadings();
    startPolling();
  },
  onremove() {
    if (state.timer) clearInterval(state.timer);
  },
  view() {
    if (!state.token) return m(LoginPage);
    return m("div.wrap", [
      topbar(),
      tabs(),
      state.page === "confirm" ? m(ConfirmPage) : m(ReadingsPage),
    ]);
  },
};

export default App;
