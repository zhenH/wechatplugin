/* 登录页逻辑 */
async function doLogin() {
  const pwd = document.getElementById("pwd").value;
  const err = document.getElementById("loginErr");
  if (!pwd) { err.textContent = "请输入密码"; return; }
  err.textContent = "";
  try {
    const res = await fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: pwd }),
    });
    if (res.ok) {
      location.href = "/";
      return;
    }
    const data = await res.json().catch(() => ({}));
    err.textContent = data.detail || "登录失败";
  } catch (e) {
    err.textContent = "网络错误：" + e.message;
  }
}

document.getElementById("loginBtn").addEventListener("click", doLogin);
document.getElementById("pwd").addEventListener("keydown", (e) => {
  if (e.key === "Enter") doLogin();
});
