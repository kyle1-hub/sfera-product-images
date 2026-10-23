/* 浏览器内：按用户密码加密各自可见行。无服务器。 */
const te = new TextEncoder();
const td = new TextDecoder();

function b64enc(buf) {
  const bytes = buf instanceof ArrayBuffer ? new Uint8Array(buf) : buf;
  let s = "";
  bytes.forEach(b => { s += String.fromCharCode(b); });
  return btoa(s);
}
function b64dec(str) {
  const bin = atob(str);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

async function deriveKey(password, salt) {
  const base = await crypto.subtle.importKey("raw", te.encode(password), "PBKDF2", false, ["deriveKey"]);
  return crypto.subtle.deriveKey(
    { name: "PBKDF2", salt, iterations: 120000, hash: "SHA-256" },
    base,
    { name: "AES-GCM", length: 256 },
    false,
    ["encrypt", "decrypt"]
  );
}

async function encryptJson(obj, password) {
  const salt = crypto.getRandomValues(new Uint8Array(16));
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const key = await deriveKey(password, salt);
  const cipher = await crypto.subtle.encrypt({ name: "AES-GCM", iv }, key, te.encode(JSON.stringify(obj)));
  return { salt: b64enc(salt), iv: b64enc(iv), data: b64enc(cipher) };
}

async function decryptJson(pack, password) {
  const salt = b64dec(pack.salt);
  const iv = b64dec(pack.iv);
  const key = await deriveKey(password, salt);
  const plain = await crypto.subtle.decrypt({ name: "AES-GCM", iv }, key, b64dec(pack.data));
  return JSON.parse(td.decode(plain));
}

function supplierField(columns) {
  const hit = (columns || []).find(c => c.label === "供应商" || c.key === "supplier");
  return hit ? hit.key : "";
}

function supplierTokens(text) {
  const s = String(text || "").trim();
  if (!s) return [];
  const tokens = new Set();
  tokens.add(s.toUpperCase());
  for (const part of s.split(/[,，;；]+/)) {
    const p = part.trim();
    if (!p) continue;
    tokens.add(p.toUpperCase());
    const before = p.split(/[:：]/)[0].trim();
    if (before) tokens.add(before.toUpperCase());
  }
  const codes = s.toUpperCase().match(/[A-Z]\d{2,4}/g) || [];
  codes.forEach(c => tokens.add(c));
  return [...tokens];
}

function rowVisibleTo(row, username, columns) {
  const uname = String(username || "").trim().toUpperCase();
  if (!uname) return false;
  const key = supplierField(columns);
  if (key && supplierTokens(row[key]).includes(uname)) return true;
  const extra = String(row.viewers || "")
    .split(/[,，;；\s]+/)
    .map(x => x.trim().toUpperCase())
    .filter(Boolean);
  return extra.includes(uname);
}

async function buildVault(columns, rows, users) {
  const list = users.filter(u => u.username && u.password);
  const vaultUsers = [];
  const viewCols = (columns || []).filter(c => c.key !== "viewers" && c.label !== "可见用户");
  for (const u of list) {
    const uname = String(u.username).trim().toUpperCase();
    const pwd = String(u.password).trim().toUpperCase();
    const isAdmin = uname === "ADMIN";
    const mine = isAdmin ? rows : rows.filter(r => rowVisibleTo(r, uname, columns));
    const payload = {
      username: uname,
      name: u.name || uname,
      isAdmin,
      columns: viewCols,
      rows: mine.map(({ viewers, ...rest }) => rest),
    };
    if (isAdmin) {
      payload.users = list.map(x => ({
        username: String(x.username || "").trim().toUpperCase(),
        name: x.name || "",
        password: String(x.password || "").trim().toUpperCase(),
      }));
    }
    vaultUsers.push({
      username: uname,
      name: u.name || uname,
      pack: await encryptJson(payload, pwd),
    });
  }
  return { v: 1, users: vaultUsers };
}

async function unlockVault(vault, username, password) {
  const uname = String(username || "").trim().toUpperCase();
  const raw = String(password || "").trim();
  const hit = (vault.users || []).find(u => String(u.username).toUpperCase() === uname);
  if (!hit) throw new Error("账号不存在");
  const tries = [...new Set([raw.toUpperCase(), raw, raw.toLowerCase()].filter(Boolean))];
  for (const pwd of tries) {
    try {
      return await decryptJson(hit.pack, pwd);
    } catch (e) {}
  }
  throw new Error("密码不对");
}
