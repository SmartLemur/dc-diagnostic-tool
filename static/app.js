// ── Global state ──
var currentPage = 'topology';
var pageVersion = 0;
var chatSessionId = Math.random().toString(36).substr(2, 9);

// ── Navigation ──
function navigate(page) {
  currentPage = page;
  pageVersion++;
  var myVersion = pageVersion;

  document.querySelectorAll('.nav-item').forEach(function(item) {
    item.classList.remove('active');
  });
  document.getElementById('nav-' + page).classList.add('active');

  closePortDrawer();
  var content = document.getElementById('content');
  content.innerHTML = '<div class="loading-box"><div class="spinner"></div>Loading...</div>';

  switch(page) {
    case 'topology': loadTopology(myVersion); break;
    case 'servers': loadServers(myVersion); break;
    case 'switches': loadSwitches(myVersion); break;
    case 'eventlog': loadEventLog(myVersion); break;
    case 'settings': loadSettings(myVersion); break;
  }
}

function isStale(version) {
  return version !== pageVersion;
}

function setContent(version, html) {
  if (isStale(version)) return;
  document.getElementById('content').innerHTML = html;
}

// ── Topology ──
function loadTopology(version) {
  if (version === undefined) { pageVersion++; version = pageVersion; currentPage = 'topology'; }
  fetch('/api/topology')
    .then(function(r) { return r.json(); })
    .then(function(data) {
      if (isStale(version)) return;
      if (data.error) { setContent(version, '<div class="loading-box">Error: ' + data.error + '</div>'); return; }
      renderTopology(version, data);
    })
    .catch(function(e) {
      if (isStale(version)) return;
      setContent(version, '<div class="loading-box">Failed: ' + e.message + '</div>');
    });
}

function renderTopology(version, data) {
  if (isStale(version)) return;
  var nodes = data.nodes || [];
  var ports = data.switch_ports || [];

  var servers = nodes.filter(function(n) { return n.type === 'bmc' || n.type === 'server' || n.type === 'windows_server' || n.type === 'linux_server'; });
  var switches = nodes.filter(function(n) { return n.type === 'switch' || n.type === 'h3c_switch' || n.type === 'cisco_switch'; });
  var portsUp = ports.filter(function(p) { return p.status === 'UP'; });

  var html = '';
  html += '<div class="stats-row">';
  html += '<div class="stat-card blue"><div class="stat-value">' + nodes.length + '</div><div class="stat-label">Total Devices</div></div>';
  html += '<div class="stat-card green"><div class="stat-value">' + servers.length + '</div><div class="stat-label">Servers</div></div>';
  html += '<div class="stat-card blue"><div class="stat-value">' + switches.length + '</div><div class="stat-label">Switches</div></div>';
  html += '<div class="stat-card green"><div class="stat-value">' + portsUp.length + '</div><div class="stat-label">Ports UP</div></div>';
  html += '</div>';

  html += '<div class="card">';
  html += '<div class="card-title">Network Topology <button class="btn btn-outline" style="margin-left:auto;font-size:0.75rem" onclick="navigate(\'topology\')">Refresh</button></div>';
  html += '<div style="background:#0f1117;border:1px solid #2d3748;border-radius:12px;overflow:auto;min-height:400px">';
  html += renderTopologyDiagram(nodes, ports);
  html += '</div></div>';

  html += '<div class="card">';
  html += '<div class="card-title">Discovered Devices</div>';
  html += '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:0.75rem">';
  nodes.forEach(function(node) {
    var icon = getDeviceIcon(node.type);
    var borderColor = node.type === 'router' ? '#9f7aea' : node.type.includes('switch') ? '#3182ce' : '#48bb78';
    html += '<div style="background:#0f1117;border:1px solid #2d3748;border-left:3px solid ' + borderColor + ';border-radius:8px;padding:0.75rem">';
    html += '<div style="display:flex;align-items:center;gap:0.5rem;margin-bottom:0.3rem">';
    html += '<span style="font-size:1.2rem">' + icon + '</span>';
    html += '<span style="font-weight:600;font-size:0.88rem;color:#fff">' + node.name + '</span>';
    html += '</div>';
    html += '<div style="font-size:0.75rem;color:#718096">' + node.ip + ' · ' + node.mac + '</div>';
    html += '<div style="font-size:0.72rem;color:#718096;margin-top:0.2rem">' + node.type + ' · ' + node.brand + '</div>';
    html += '</div>';
  });
  html += '</div></div>';

  setContent(version, html);
}

function renderTopologyDiagram(nodes, ports) {
  var router = nodes.find(function(n) { return n.type === 'router'; });
  var switches = nodes.filter(function(n) { return n.type.includes('switch'); });
  var servers = nodes.filter(function(n) { return n.type === 'bmc' || n.type === 'windows_server' || n.type === 'linux_server' || n.type === 'server'; });
  var unknown = nodes.filter(function(n) { return n.type === 'unknown'; });
  var allBottom = servers.concat(unknown);

  var html = '<svg width="100%" height="480" xmlns="http://www.w3.org/2000/svg">';

  if (router) {
    html += drawNode(340, 50, router, '#9f7aea');
    if (switches.length > 0) {
      html += '<line x1="340" y1="95" x2="340" y2="145" stroke="#4a5568" stroke-width="2" stroke-dasharray="4"/>';
    }
  }

  var switchX = switches.length === 1 ? 340 : 200;
  var switchSpacing = switches.length > 1 ? 280 : 0;
  switches.forEach(function(sw, i) {
    var x = switchX + (i * switchSpacing);
    html += drawNode(x, 170, sw, '#3182ce');
    if (router) {
      html += '<line x1="340" y1="95" x2="' + x + '" y2="145" stroke="#4a5568" stroke-width="2" stroke-dasharray="4"/>';
    }
  });

  var startX = allBottom.length > 0 ? Math.max(60, 340 - ((allBottom.length - 1) * 130) / 2) : 340;
  allBottom.forEach(function(node, i) {
    var x = startX + (i * 130);
    var color = node.type === 'unknown' ? '#718096' : '#48bb78';
    html += drawNode(x, 340, node, color);
    if (switches.length > 0) {
      var swX = switchX;
      html += '<line x1="' + x + '" y1="315" x2="' + swX + '" y2="195" stroke="#4a5568" stroke-width="1.5" stroke-dasharray="3"/>';
    }
  });

  html += '</svg>';
  return html;
}

function drawNode(x, y, node, color) {
  var icon = getDeviceIcon(node.type);
  var name = node.name.length > 14 ? node.name.substr(0, 14) + '...' : node.name;
  var html = '';
  html += '<rect x="' + (x-55) + '" y="' + (y-32) + '" width="110" height="64" rx="8" fill="#1a1d2e" stroke="' + color + '" stroke-width="2"/>';
  html += '<text x="' + x + '" y="' + (y-8) + '" text-anchor="middle" font-size="20">' + icon + '</text>';
  html += '<text x="' + x + '" y="' + (y+12) + '" text-anchor="middle" fill="#ffffff" font-size="10" font-weight="600">' + name + '</text>';
  html += '<text x="' + x + '" y="' + (y+25) + '" text-anchor="middle" fill="#718096" font-size="9">' + node.ip + '</text>';
  return html;
}

function getDeviceIcon(type) {
  if (type === 'router') return '🌐';
  if (type.includes('switch')) return '🔀';
  if (type === 'bmc') return '🖥';
  if (type === 'windows_server') return '🪟';
  if (type === 'linux_server') return '🐧';
  return '❓';
}

// ── Servers ──
function loadServers(version) {
  if (version === undefined) { pageVersion++; version = pageVersion; currentPage = 'servers'; }
  fetch('/api/servers')
    .then(function(r) { return r.json(); })
    .then(function(data) {
      if (isStale(version)) return;
      var html = '';
      html += '<div class="section-header">';
      html += '<span class="section-title">Server Hardware Health</span>';
      html += '<button class="btn btn-primary" style="margin-left:auto" onclick="showAddDevice(\'bmc\')">+ Add Server</button>';
      html += '</div>';
      html += '<div class="server-grid">';
      var keys = Object.keys(data);
      if (keys.length === 0) {
        html += '<div class="loading-box">No servers found.</div>';
      }
      keys.forEach(function(key) {
        var s = data[key];
        if (s.error) {
          html += '<div class="server-card"><div class="server-name">' + key + '</div><div style="color:#fc8181;font-size:0.8rem">Error: ' + s.error + '</div></div>';
          return;
        }
        var health = s.overall_health || 'Unknown';
        var cardClass = health === 'OK' ? 'ok' : health === 'Warning' ? 'warning' : 'critical';
        var powerClass = s.power_state === 'On' ? 'power-on' : 'power-off';
        var badges = '';
        var hd = s.health_details || {};
        Object.keys(hd).forEach(function(hk) {
          var hv = hd[hk];
          var bc = hv === 'OK' ? 'hb-ok' : hv === 'Warning' ? 'hb-warn' : hv === 'Critical' ? 'hb-crit' : 'hb-unk';
          badges += '<span class="hb ' + bc + '">' + hk.replace('_', ' ') + ': ' + hv + '</span>';
        });
        html += '<div class="server-card ' + cardClass + '">';
        html += '<div class="server-name">🖥 ' + (s.name || key) + '</div>';
        html += '<div class="server-model">' + (s.model || '') + ' · ' + s.ram_gb + 'GB RAM</div>';
        html += '<span class="power-badge ' + powerClass + '">' + s.power_state + '</span>';
        html += '<div class="health-grid">' + badges + '</div>';
        html += '</div>';
      });
      html += '</div>';
      setContent(version, html);
    })
    .catch(function(e) {
      if (isStale(version)) return;
      setContent(version, '<div class="loading-box">Failed: ' + e.message + '</div>');
    });
}

// ── Switches ──
function loadSwitches(version) {
  if (version === undefined) { pageVersion++; version = pageVersion; currentPage = 'switches'; }
  fetch('/api/switch')
    .then(function(r) { return r.json(); })
    .then(function(data) {
      if (isStale(version)) return;
      var html = '';
      html += '<div class="section-header">';
      html += '<span class="section-title">Switch Port Status</span>';
      html += '<button class="btn btn-outline" style="margin-left:auto" onclick="document.getElementById(\'chatInput\').value=\'show full config\';sendChat()">Full Switch Config</button>';
      html += '<button class="btn btn-primary" onclick="showAddDevice(\'switch\')">+ Add Switch</button>';
      html += '</div>';
      if (data.error) {
        setContent(version, html + '<div class="loading-box">Error: ' + data.error + '</div>');
        return;
      }
      html += '<div style="display:flex;gap:0.75rem;margin-bottom:1rem">';
      html += '<span class="hb hb-ok">UP: ' + data.up_count + '</span>';
      html += '<span class="hb hb-unk">DOWN: ' + data.down_count + '</span>';
      html += '<span style="font-size:0.75rem;color:#718096;margin-left:auto">Total: ' + data.total_ports + ' ports</span>';
      html += '</div>';
      html += '<div class="card">';
      html += '<div class="card-title">' + (data.name || 'Switch') + (data.ip ? ' — ' + data.ip : '') + '</div>';
      html += '<div class="port-grid">';
      var allPorts = (data.up_ports || []).concat(data.down_ports || []);
      allPorts.sort(function(a, b) { return a.interface.localeCompare(b.interface); });
      allPorts.forEach(function(p) {
        var isUp = p.status === 'UP';
        var devices = p.devices || [];
        var deviceName = devices.length > 0 ? (devices[0].name || devices[0].ip || '') : '';
        html += '<div class="port-card ' + (isUp ? 'up' : 'down') + '" style="cursor:pointer" onclick="openPortDrawer(\'' + p.interface + '\')">';
        html += '<div class="port-name">' + p.interface + '</div>';
        html += '<div class="port-info">' + p.status + ' · ' + p.speed + ' · VLAN ' + p.vlan + '</div>';
        if (deviceName) html += '<div class="port-device">' + deviceName + '</div>';
        html += '</div>';
      });
      html += '</div></div>';
      setContent(version, html);
    })
    .catch(function(e) {
      if (isStale(version)) return;
      setContent(version, '<div class="loading-box">Failed: ' + e.message + '</div>');
    });
}

// ── Event Log ──
function loadEventLog(version) {
  if (version === undefined) { pageVersion++; version = pageVersion; currentPage = 'eventlog'; }
  fetch('/api/events')
    .then(function(r) { return r.json(); })
    .then(function(data) {
      if (isStale(version)) return;
      var html = '';
      html += '<div class="section-header">';
      html += '<span class="section-title">Event Log</span>';
      html += '<button class="btn btn-outline" style="margin-left:auto" onclick="navigate(\'eventlog\')">Refresh</button>';
      html += '</div>';
      html += '<div class="event-list">';
      if (!data.events || data.events.length === 0) {
        html += '<div class="loading-box">No events recorded yet.</div>';
      } else {
        data.events.forEach(function(ev) {
          var typeClass = ev.level === 'warning' ? 'warning' : ev.level === 'error' ? 'error' : ev.level === 'success' ? 'success' : 'info';
          var icon = ev.level === 'warning' ? '⚠' : ev.level === 'error' ? '✗' : ev.level === 'success' ? '✓' : 'ℹ';
          html += '<div class="event-item ' + typeClass + '">';
          html += '<span class="event-icon">' + icon + '</span>';
          html += '<div><div class="event-text">' + ev.message + '</div>';
          html += '<div class="event-time">' + ev.timestamp + ' · ' + ev.username + '</div></div>';
          html += '</div>';
        });
      }
      html += '</div>';
      setContent(version, html);
    })
    .catch(function(e) {
      if (isStale(version)) return;
      setContent(version, '<div class="loading-box">Failed: ' + e.message + '</div>');
    });
}

// ── Settings ──
function loadSettings(version) {
  if (version === undefined) { pageVersion++; version = pageVersion; currentPage = 'settings'; }
  fetch('/api/devices')
    .then(function(r) { return r.json(); })
    .then(function(data) {
      if (isStale(version)) return;
      var html = '';
      html += '<div class="card">';
      html += '<div class="card-title">Devices <button class="btn btn-primary" style="margin-left:auto;font-size:0.75rem" onclick="showAddDevice()">+ Add Device</button></div>';
      if (!data.devices || data.devices.length === 0) {
        html += '<div style="color:#718096;font-size:0.85rem">No devices added yet.</div>';
      } else {
        html += '<div style="display:flex;flex-direction:column;gap:0.5rem">';
        data.devices.forEach(function(d) {
          html += '<div style="display:flex;align-items:center;gap:0.75rem;padding:0.75rem;background:#0f1117;border-radius:8px">';
          html += '<span style="font-size:1.2rem">' + getDeviceIcon(d.type) + '</span>';
          html += '<div style="flex:1"><div style="font-size:0.88rem;font-weight:600;color:#fff">' + d.name + '</div>';
          html += '<div style="font-size:0.75rem;color:#718096">' + d.ip + ' · ' + d.type + ' · ' + d.brand + '</div></div>';
          html += '<button class="btn btn-outline" style="font-size:0.75rem" onclick="deleteDevice(' + d.id + ')">Delete</button>';
          html += '</div>';
        });
        html += '</div>';
      }
      html += '</div>';
      html += '<div class="card"><div class="card-title">Change Password</div>';
      html += '<div style="max-width:400px">';
      html += '<div class="form-group"><label>Current Password</label><input type="password" id="currentPass"></div>';
      html += '<div class="form-group"><label>New Password</label><input type="password" id="newPass"></div>';
      html += '<div class="form-group"><label>Confirm Password</label><input type="password" id="confirmPass"></div>';
      html += '<button class="btn btn-primary" onclick="changePassword()">Update Password</button>';
      html += '</div></div>';
      setContent(version, html);
    })
    .catch(function(e) {
      if (isStale(version)) return;
      setContent(version, '<div class="loading-box">Failed: ' + e.message + '</div>');
    });
}

// ── Add Device Modal ──
function showAddDevice(type) {
  document.getElementById('modal-overlay').classList.add('open');
  if (type) document.getElementById('deviceType').value = type;
}

function closeModal() {
  document.getElementById('modal-overlay').classList.remove('open');
  document.getElementById('addDeviceForm').reset();
}

function saveDevice() {
  var name = document.getElementById('deviceName').value.trim();
  var type = document.getElementById('deviceType').value;
  var ip = document.getElementById('deviceIp').value.trim();
  var username = document.getElementById('deviceUsername').value.trim();
  var password = document.getElementById('devicePassword').value.trim();
  if (!name || !ip) { alert('Name and IP are required'); return; }
  fetch('/api/devices', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({name: name, type: type, ip: ip, username: username, password: password})
  })
  .then(function(r) { return r.json(); })
  .then(function(data) {
    if (data.success) { closeModal(); navigate(currentPage); }
    else alert('Error: ' + data.error);
  });
}

function deleteDevice(id) {
  if (!confirm('Delete this device?')) return;
  fetch('/api/devices/' + id, {method: 'DELETE'})
    .then(function(r) { return r.json(); })
    .then(function(data) { if (data.success) navigate('settings'); });
}

function changePassword() {
  var current = document.getElementById('currentPass').value;
  var newPass = document.getElementById('newPass').value;
  var confirm = document.getElementById('confirmPass').value;
  if (newPass !== confirm) { alert('Passwords do not match'); return; }
  fetch('/api/change-password', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({current: current, new: newPass})
  })
  .then(function(r) { return r.json(); })
  .then(function(data) {
    if (data.success) alert('Password changed successfully');
    else alert('Error: ' + data.error);
  });
}

// ── Chatbot ──
function addChatMsg(text, type) {
  var box = document.getElementById('chatMessages');
  var d = document.createElement('div');
  d.className = 'msg ' + type;
  var parts = text.split('**');
  var result = '';
  for (var i = 0; i < parts.length; i++) {
    result += i % 2 === 1 ? '<strong>' + parts[i] + '</strong>' : parts[i];
  }
  result = result.split('\n').join('<br>');
  d.innerHTML = result;
  box.appendChild(d);
  box.scrollTop = box.scrollHeight;
  return d;
}

function addReportBtn(data) {
  var box = document.getElementById('chatMessages');
  var btn = document.createElement('button');
  btn.className = 'report-btn';
  btn.textContent = 'View Full Report';
  btn.onclick = function() {
    var p = new URLSearchParams({
      target_ip: data.target_ip || '',
      gateway_ip: data.gateway_ip || '',
      dns_server: data.dns_server || '8.8.8.8',
      username: data.username || '',
      password: data.password || ''
    });
    window.open('/report?' + p.toString(), '_blank');
  };
  box.appendChild(btn);
  box.scrollTop = box.scrollHeight;
}

function sendChat() {
  var input = document.getElementById('chatInput');
  var msg = input.value.trim();
  if (!msg) return;
  input.value = '';
  addChatMsg(msg, 'user');
  var thinking = addChatMsg('Thinking...', 'thinking');
  fetch('/chat', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({message: msg, session_id: chatSessionId})
  })
  .then(function(r) { return r.json(); })
  .then(function(data) {
    thinking.remove();
    addChatMsg(data.reply, 'ai');
    if (data.action === 'show_full_config') {
      openConfigDrawer(data.config || '');
    }
    if (data.run_diagnostic && data.data) {
      var running = addChatMsg('Running checks... please wait', 'thinking');
      fetch('/run_diagnostic', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(Object.assign({}, data.data, {session_id: chatSessionId}))
      })
      .then(function(r) { return r.json(); })
      .then(function(d2) {
        running.remove();
        addChatMsg(d2.reply, 'ai');
        if (d2.show_report_btn) addReportBtn(d2);
      });
    }
  })
  .catch(function(e) {
    thinking.remove();
    addChatMsg('Connection error. Please try again.', 'ai');
  });
}

// ── Port Drawer ──
var currentDrawerInterface = null;

function openPortDrawer(iface) {
  if (currentPage !== 'switches') return;
  currentDrawerInterface = iface;
  document.getElementById('port-drawer').classList.add('open');
  document.getElementById('port-drawer-overlay').classList.add('open');
  document.getElementById('port-drawer-title').textContent = iface;
  document.getElementById('port-drawer-content').innerHTML = '<div style="flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:0.5rem;color:#718096;font-size:0.82rem"><div class="spinner"></div>Loading...</div>';
  fetch('/api/switch/port/' + iface, {credentials: 'include'})
    .then(function(r) { return r.json(); })
    .then(function(data) {
      if (data.error) {
        document.getElementById('port-drawer-content').innerHTML = '<div style="color:#fc8181;padding:1rem">Error: ' + data.error + '</div>';
        return;
      }
      var d = data.details || {};
      var html = '';
      html += '<div class="drawer-section"><div class="drawer-section-title">Port Details</div>';
      html += '<div class="detail-grid">';
      html += '<div class="detail-item"><div class="detail-label">Status</div><div class="detail-value" style="color:' + (d.status === 'UP' ? '#48bb78' : '#718096') + '">' + (d.status || 'Unknown') + '</div></div>';
      html += '<div class="detail-item"><div class="detail-label">Speed</div><div class="detail-value">' + (d.speed || 'Unknown') + '</div></div>';
      html += '<div class="detail-item"><div class="detail-label">VLAN</div><div class="detail-value">' + (d.vlan || 'Unknown') + '</div></div>';
      html += '<div class="detail-item"><div class="detail-label">Type</div><div class="detail-value">' + (d.type || 'Unknown') + '</div></div>';
      if (d.description) html += '<div class="detail-item" style="grid-column:span 2"><div class="detail-label">Description</div><div class="detail-value">' + d.description + '</div></div>';
      html += '</div></div>';
      if (data.raw_config) {
        html += '<div class="drawer-section"><div class="drawer-section-title">Raw Config</div>';
        html += '<pre class="drawer-raw-config">' + data.raw_config.replace(/&/g,'&amp;').replace(/</g,'&lt;') + '</pre></div>';
      }
      html += '<div class="drawer-section"><div class="drawer-section-title">Change VLAN</div>';
      html += '<div style="display:flex;gap:0.5rem;align-items:center">';
      html += '<input type="number" id="drawer-vlan-input" placeholder="VLAN ID" min="1" max="4094" style="background:#0f1117;border:1px solid #2d3748;border-radius:6px;padding:0.5rem 0.65rem;color:#e2e8f0;font-size:0.85rem;width:120px;outline:none">';
      html += '<button class="btn btn-primary" style="font-size:0.8rem;padding:0.45rem 0.9rem" onclick="drawerChangeVlan()">Apply</button>';
      html += '</div></div>';
      document.getElementById('port-drawer-content').innerHTML = html;
    })
    .catch(function() {
      document.getElementById('port-drawer-content').innerHTML = '<div style="color:#fc8181;padding:1rem">Connection error. Please try again.</div>';
    });
}

function openConfigDrawer(config) {
  document.getElementById('port-drawer').classList.add('open');
  document.getElementById('port-drawer-overlay').classList.add('open');
  document.getElementById('port-drawer-title').textContent = 'Full Switch Configuration';
  var escaped = config.replace(/&/g, '&amp;').replace(/</g, '&lt;');
  document.getElementById('port-drawer-content').innerHTML =
    '<div class="drawer-section">' +
    '<div class="drawer-section-title">Running Configuration</div>' +
    '<pre class="drawer-raw-config">' + escaped + '</pre>' +
    '</div>';
}

function closePortDrawer() {
  document.getElementById('port-drawer').classList.remove('open');
  document.getElementById('port-drawer-overlay').classList.remove('open');
}

function drawerChangeVlan() {
  var vlanId = document.getElementById('drawer-vlan-input').value.trim();
  if (!vlanId) { alert('Enter a VLAN ID'); return; }
  var iface = currentDrawerInterface;
  closePortDrawer();
  var chatInput = document.getElementById('chatInput');
  chatInput.value = 'set port ' + iface + ' to vlan ' + vlanId;
  sendChat();
}

// ── Chat history restore ──
var chatHistoryLoaded = false;

function loadChatHistory() {
  if (chatHistoryLoaded) return;
  chatHistoryLoaded = true;
  fetch('/api/session/history', {credentials: 'include'})
    .then(function(r) { return r.json(); })
    .then(function(data) {
      var history = data.history || [];
      history.forEach(function(item) {
        if (item.user) addChatMsg(item.user, 'user');
        if (item.reply) addChatMsg(item.reply, 'ai');
      });
    })
    .catch(function() {});
}

// ── Key bindings ──
document.addEventListener('keydown', function(e) {
  if (e.target.id === 'chatInput' && e.key === 'Enter') sendChat();
});

// Load chat history once on page load
loadChatHistory();

// ── Auto refresh — only refreshes current page ──
setInterval(function() {
  pageVersion++;
  var v = pageVersion;
  switch(currentPage) {
    case 'servers': loadServers(v); break;
    case 'switches': loadSwitches(v); break;
    case 'eventlog': loadEventLog(v); break;
  }
}, 30000);
