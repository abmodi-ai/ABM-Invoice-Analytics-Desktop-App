//! Verismo desktop shell.
//!
//! Spawns the Python engine as a sidecar bound to 127.0.0.1 on a random port with a random
//! 256-bit bearer token passed through environment variables, supervises it (restart with
//! backoff on crash; immediate restart after a backup restore, exit code 75), and hands the
//! URL + token to the UI through the `engine_info` command. Nothing listens on a non-loopback
//! interface and the webview CSP only allows connections to 127.0.0.1.

use serde::Serialize;
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpListener, TcpStream};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};
use tauri::{Emitter, Manager, RunEvent};

const RESTART_EXIT_CODE: i32 = 75;

#[derive(Clone, Serialize)]
struct EngineInfo {
    url: String,
    token: String,
}

#[derive(Clone, Serialize)]
struct EngineStatus {
    state: String,
    detail: String,
    restarts: u32,
}

struct Engine {
    port: u16,
    token: String,
    child: Mutex<Option<Child>>,
    shutting_down: Mutex<bool>,
    restarts: Mutex<u32>,
}

fn random_token() -> String {
    let mut buf = [0u8; 32];
    getrandom::fill(&mut buf).expect("OS random source unavailable");
    buf.iter().map(|b| format!("{b:02x}")).collect()
}

fn free_loopback_port() -> u16 {
    TcpListener::bind("127.0.0.1:0")
        .and_then(|l| l.local_addr())
        .map(|a| a.port())
        .expect("no free loopback port")
}

/// Minimal HTTP GET /health over loopback (no HTTP client dependency, no proxies).
fn healthy(port: u16) -> bool {
    let addr = SocketAddr::from(([127, 0, 0, 1], port));
    let Ok(mut s) = TcpStream::connect_timeout(&addr, Duration::from_millis(300)) else {
        return false;
    };
    let _ = s.set_read_timeout(Some(Duration::from_millis(800)));
    if s
        .write_all(b"GET /health HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
        .is_err()
    {
        return false;
    }
    let mut buf = [0u8; 64];
    matches!(s.read(&mut buf), Ok(n) if n > 12 && &buf[9..12] == b"200")
}

/// Where the engine lives: the bundled PyInstaller one-dir build in release; `uv run` in dev.
fn engine_command(app: &tauri::AppHandle) -> Command {
    if let Ok(custom) = std::env::var("VERISMO_ENGINE_CMD") {
        let mut parts = custom.split_whitespace();
        let mut c = Command::new(parts.next().unwrap_or("verismo-engine"));
        c.args(parts);
        return c;
    }
    let exe = if cfg!(windows) { "verismo-engine.exe" } else { "verismo-engine" };
    if let Ok(res) = app.path().resource_dir() {
        let bundled: PathBuf = res.join("engine").join(exe);
        if bundled.exists() {
            let mut c = Command::new(bundled);
            c.current_dir(res.join("engine"));
            return c;
        }
    }
    // Development: run the engine from the repository with uv.
    let repo = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../..");
    let mut c = Command::new("uv");
    c.args(["run", "python", "-m", "verismo_engine"]).current_dir(repo);
    c
}

fn spawn(app: &tauri::AppHandle, eng: &Engine) -> std::io::Result<Child> {
    let mut cmd = engine_command(app);
    cmd.env("VERISMO_PORT", eng.port.to_string())
        .env("VERISMO_TOKEN", &eng.token)
        .env("PYTHONUNBUFFERED", "1")
        .env("VERISMO_PARENT_PID", std::process::id().to_string())
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    cmd.spawn()
}

fn supervise(app: tauri::AppHandle, eng: Arc<Engine>) {
    thread::spawn(move || {
        let mut backoff = Duration::from_secs(1);
        loop {
            match spawn(&app, &eng) {
                Ok(child) => {
                    *eng.child.lock().unwrap() = Some(child);
                    let _ = app.emit("engine-status", EngineStatus { state: "starting".into(), detail: String::new(), restarts: *eng.restarts.lock().unwrap() });
                }
                Err(e) => {
                    let _ = app.emit("engine-status", EngineStatus { state: "error".into(), detail: format!("could not start engine: {e}"), restarts: *eng.restarts.lock().unwrap() });
                    thread::sleep(backoff);
                    backoff = (backoff * 2).min(Duration::from_secs(30));
                    continue;
                }
            }
            let started = Instant::now();
            // Wait for exit without holding the lock (so shutdown can kill it).
            let code = loop {
                thread::sleep(Duration::from_millis(500));
                let mut guard = eng.child.lock().unwrap();
                match guard.as_mut().map(|c| c.try_wait()) {
                    Some(Ok(Some(status))) => break status.code().unwrap_or(-1),
                    Some(Ok(None)) => continue,
                    _ => break -1,
                }
            };
            if *eng.shutting_down.lock().unwrap() {
                return;
            }
            *eng.restarts.lock().unwrap() += 1;
            if code == RESTART_EXIT_CODE {
                backoff = Duration::from_secs(1);
                continue; // restored database: come straight back
            }
            if started.elapsed() > Duration::from_secs(60) {
                backoff = Duration::from_secs(1);
            }
            let _ = app.emit("engine-status", EngineStatus { state: "crashed".into(), detail: format!("engine exited with code {code}; restarting in {}s", backoff.as_secs()), restarts: *eng.restarts.lock().unwrap() });
            thread::sleep(backoff);
            backoff = (backoff * 2).min(Duration::from_secs(30));
        }
    });
}

#[tauri::command]
async fn engine_info(state: tauri::State<'_, Arc<Engine>>) -> Result<EngineInfo, String> {
    let eng = state.inner().clone();
    let port = eng.port;
    let ok = tauri::async_runtime::spawn_blocking(move || {
        let deadline = Instant::now() + Duration::from_secs(90);
        while Instant::now() < deadline {
            if healthy(port) {
                return true;
            }
            thread::sleep(Duration::from_millis(250));
        }
        false
    })
    .await
    .map_err(|e| e.to_string())?;
    if !ok {
        return Err("The Verismo engine did not start. See the log folder for details.".into());
    }
    Ok(EngineInfo { url: format!("http://127.0.0.1:{}", eng.port), token: eng.token.clone() })
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let eng = Arc::new(Engine {
        port: free_loopback_port(),
        token: random_token(),
        child: Mutex::new(None),
        shutting_down: Mutex::new(false),
        restarts: Mutex::new(0),
    });
    let for_setup = eng.clone();
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage(eng.clone())
        .invoke_handler(tauri::generate_handler![engine_info])
        .setup(move |app| {
            supervise(app.handle().clone(), for_setup.clone());
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building the Verismo app");
    app.run(move |_handle, event| {
        if let RunEvent::Exit = event {
            *eng.shutting_down.lock().unwrap() = true;
            if let Some(mut c) = eng.child.lock().unwrap().take() {
                let _ = c.kill();
                let _ = c.wait();
            }
        }
    });
}
