"use strict";
const $ = id => document.getElementById(id);
let csrf = "", chosen = null, busy = false, polling = false, lastState = "", lastList = "";
const notice = message => { $("notice").textContent = message; };
async function api(path, data, timeout = 12000) {
  const response = await fetch(path, {method: data === undefined ? "GET" : "POST",
    headers: data === undefined ? {} : {"Content-Type":"application/json", "X-CSRF-Token":csrf},
    body: data === undefined ? undefined : JSON.stringify(data), cache:"no-store",
    signal: AbortSignal.timeout(timeout)});
  const body = await response.json();
  if (response.status === 401) { csrf = ""; showLogin(); }
  if (!response.ok) throw new Error(body.error || "Không thực hiện được. Vui lòng thử lại.");
  return body;
}
function showLogin() {
  $("loginCard").hidden = false; $("dashboard").hidden = true;
}
function button(text, onClick, kind = "secondary") {
  const element = document.createElement("button");
  element.textContent = text; element.className = kind; element.onclick = onClick;
  return element;
}
function row(title, detail) {
  const element = document.createElement("div"); element.className = "network";
  const content = document.createElement("div"), heading = document.createElement("strong"), info = document.createElement("small");
  heading.textContent = title; info.textContent = detail;
  content.append(heading, info); element.append(content); return element;
}
function choose(network) {
  chosen = network; $("joinTitle").textContent = network.ssid;
  $("wifiPassword").value = ""; $("wifiPassword").type = "password"; $("showPassword").textContent = "Hiện";
  $("wifiPassword").required = !network.saved;
  $("savedHint").textContent = network.saved ? "Đã lưu mật khẩu. Để trống để dùng lại hoặc nhập mật khẩu mới." : "Nhập mật khẩu của mạng này.";
  $("join").showModal();
}
function render(state) {
  busy = state.state === "connecting";
  const labels = {setup:"SẴN SÀNG CÀI ĐẶT", connecting:"ĐANG KẾT NỐI", connected:"ĐÃ KẾT NỐI", reconnecting:"ĐANG KẾT NỐI LẠI", error:"CẦN KIỂM TRA"};
  $("statusLabel").textContent = labels[state.state] || state.state;
  $("current").textContent = state.ssid || "Chưa kết nối";
  $("message").textContent = state.message; $("ipBox").hidden = !state.ip;
  $("ssh").textContent = state.ip ? "ssh root@" + state.ip : "";
  $("setup").textContent = "Wi-Fi cài đặt: " + state.setup_ssid + " · " + state.setup_url;
  $("scan").disabled = busy || state.scanning;
  $("scan").textContent = state.scanning ? "Đang dò…" : "Dò lại";
  const listKey = JSON.stringify([state.networks, state.saved, state.ssid, state.state]);
  if (listKey === lastList) return;
  lastList = listKey;
  const networks = [...state.networks].sort((a,b) => Number(b.saved)-Number(a.saved) || b.signal-a.signal);
  $("networks").replaceChildren();
  for (const network of networks) {
    const connected = network.ssid === state.ssid && state.state === "connected";
    const strength = network.signal >= -60 ? "Sóng mạnh" : network.signal >= -75 ? "Sóng vừa" : "Sóng yếu";
    const detail = strength + (network.saved ? " · Đã lưu" : "") + (!network.supported ? " · Chưa hỗ trợ bảo mật này" : "");
    const element = row(network.ssid, detail);
    const connect = button(connected ? "Đang dùng" : "Kết nối", () => choose(network), connected ? "secondary" : "primary");
    connect.disabled = busy || !network.supported || connected;
    element.append(connect); $("networks").append(element);
  }
  if (!networks.length) $("networks").textContent = "Chưa thấy Wi-Fi. Bật mạng 2.4 GHz rồi nhấn Dò lại.";
  $("saved").replaceChildren();
  for (const profile of state.saved) {
    const element = row(profile.ssid, "Mật khẩu được lưu trên thiết bị");
    const forget = button("Quên", async () => {
      if (!window.confirm("Quên Wi-Fi " + profile.ssid + "?")) return;
      try { await api("/api/forget", {ssid:profile.ssid}); notice("Đã gửi yêu cầu quên mạng."); }
      catch (error) { notice(error.message); }
    }, "textButton");
    forget.disabled = busy; element.append(forget); $("saved").append(element);
  }
  if (!state.saved.length) $("saved").textContent = "Chưa lưu mạng nào.";
}
let activating = false;
function renderMqtt(info) {
  const links = {connected:"đang kết nối", connecting:"đang đăng nhập broker", stopped:"chương trình chưa chạy"};
  $("mqttStatus").textContent = !info.installed ? "Chưa cài chương trình MQTT trên thiết bị."
    : info.activated ? "Đã kích hoạt: " + info.device_id + " · MQTT " + (links[info.link] || info.link)
    : "Chưa kích hoạt. Nhập 2 mã bên dưới để kết nối MQTT.";
  $("mqttForm").querySelector("button").disabled = activating || !info.installed;
  if (info.activated && !$("mqttDevice").value) $("mqttDevice").value = info.device_id;
}
async function refresh() {
  if (!csrf || polling) return;
  polling = true;
  try {
    const state = await api("/api/status"); render(state);
    try { renderMqtt(await api("/api/mqtt")); } catch (_) { $("mqttCard").hidden = true; }
    if (lastState === "offline") notice("Đã nối lại với thiết bị.");
    lastState = state.state;
  } catch (error) {
    lastState = "offline";
    notice(csrf ? "Chưa liên lạc được với MIRA. Kết nối lại Wi-Fi cài đặt; trang sẽ tự thử lại." : error.message);
  } finally { polling = false; }
}
$("loginForm").onsubmit = async event => {
  event.preventDefault();
  const submit = event.submitter; submit.disabled = true;
  try {
    const data = await api("/api/login", {password:$("adminPassword").value});
    csrf = data.csrf; $("adminPassword").value = ""; $("loginCard").hidden = true; $("dashboard").hidden = false; notice(""); await refresh();
  } catch (error) { notice(error.message); } finally { submit.disabled = false; }
};
$("joinForm").onsubmit = async event => {
  event.preventDefault();
  const password = $("wifiPassword").value;
  if (password && !((password.length >= 8 && password.length <= 63 && /^[\x20-\x7e]+$/.test(password)) || /^[0-9a-fA-F]{64}$/.test(password))) {
    notice("Mật khẩu cần 8–63 ký tự ASCII hoặc 64 ký tự hex."); return;
  }
  event.submitter.disabled = true;
  try {
    await api("/api/connect", {ssid:chosen.ssid, security:"psk2", password:password || null});
    $("wifiPassword").value = ""; $("join").close();
    notice("Đang thử kết nối. Nếu trang tạm mất liên lạc, hãy đợi rồi nối lại Wi-Fi cài đặt.");
  } catch (error) { notice(error.message); } finally { event.submitter.disabled = false; }
};
$("mqttForm").onsubmit = async event => {
  event.preventDefault();
  const button = event.submitter; button.disabled = true; activating = true;
  notice("Đang kích hoạt MQTT, tối đa 1 phút…");
  try {
    const result = await api("/api/mqtt/activate",
      {device_id:$("mqttDevice").value.trim().toUpperCase(), code:$("mqttCode").value.trim().toUpperCase()}, 90000);
    $("mqttCode").value = ""; notice(result.message);
  } catch (error) { notice(error.message); } finally { activating = false; button.disabled = false; }
};
$("cancel").onclick =() => { $("wifiPassword").value = ""; $("join").close(); };
$("showPassword").onclick = () => {
  const hidden = $("wifiPassword").type === "password";
  $("wifiPassword").type = hidden ? "text" : "password"; $("showPassword").textContent = hidden ? "Ẩn" : "Hiện";
};
$("scan").onclick = async () => { try { await api("/api/scan", {}); notice("Đã yêu cầu dò mạng."); } catch (error) { notice(error.message); } };
$("logout").onclick = async () => { try { await api("/api/logout", {}); csrf = ""; showLogin(); } catch (error) { notice(error.message); } };
(async () => { try { csrf = (await api("/api/session")).csrf; $("loginCard").hidden = true; $("dashboard").hidden = false; await refresh(); } catch (_) { showLogin(); } })();
setInterval(refresh, 3000);
