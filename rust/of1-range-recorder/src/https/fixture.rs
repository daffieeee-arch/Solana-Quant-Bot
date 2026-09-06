//! Sealed numeric-loopback TLS scripts, shared by tests and the visible fixture report.
//! Certificates and signing material are generated in memory and never persisted/logged.

use super::{Deadline, DeadlineTcp, HttpsError, HttpsResult, MAX_HEADER_BYTES};
use rustls::{ServerConfig, ServerConnection, StreamOwned, pki_types::PrivatePkcs8KeyDer};
use std::{
    io::{self, Read, Write},
    net::TcpListener,
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
    thread::{self, JoinHandle},
    time::Duration,
};

/// A bounded exact response; mismatched framing is permitted only for adversarial fixtures.
pub struct ResponseScript {
    pub header: Vec<u8>,
    pub body: Vec<u8>,
    pub fragment_bytes: usize,
    pub delay_ms: u64,
    pub close_notify: bool,
}

pub struct FixtureServer {
    port: u16,
    root_der: Vec<u8>,
    stopped: Arc<AtomicBool>,
    worker: Option<JoinHandle<HttpsResult<Vec<String>>>>,
}

impl FixtureServer {
    /// Bind only 127.0.0.1, with no external DNS or configuration.
    /// # Errors
    /// Rejects unbounded scripts or certificate/server setup errors.
    pub fn start(certificate_name: &str, scripts: Vec<ResponseScript>) -> HttpsResult<Self> {
        Self::start_paced(certificate_name, scripts, 0)
    }

    /// Paced local fragments demonstrate actual intra-request observations, not an animated counter.
    /// # Errors
    /// Rejects the usual script bounds or a fragment delay above 500 ms.
    pub fn start_paced(
        certificate_name: &str,
        scripts: Vec<ResponseScript>,
        fragment_delay_ms: u64,
    ) -> HttpsResult<Self> {
        if scripts.is_empty()
            || fragment_delay_ms > 500
            || scripts.len() > 16
            || certificate_name.len() > 128
            || scripts.iter().any(|script| {
                script.header.len() > MAX_HEADER_BYTES + 1
                    || script.fragment_bytes == 0
                    || script.fragment_bytes > 65_536
                    || script.delay_ms > 500
            })
            || scripts
                .iter()
                .try_fold(0usize, |sum, s| sum.checked_add(s.body.len()))
                .is_none_or(|size| size > 32 * 1024 * 1024)
        {
            return Err(HttpsError::Fixture);
        }
        let rcgen::CertifiedKey { cert, signing_key } =
            rcgen::generate_simple_self_signed(vec![certificate_name.into()])
                .map_err(|_| HttpsError::Fixture)?;
        let root_der = cert.der().to_vec();
        let config =
            ServerConfig::builder_with_provider(Arc::new(rustls::crypto::ring::default_provider()))
                .with_safe_default_protocol_versions()?
                .with_no_client_auth()
                .with_single_cert(
                    vec![cert.der().clone()],
                    PrivatePkcs8KeyDer::from(signing_key.serialize_der()).into(),
                )?;
        let listener = TcpListener::bind((std::net::Ipv4Addr::LOCALHOST, 0))?;
        let port = listener.local_addr()?.port();
        listener.set_nonblocking(true)?;
        let stopped = Arc::new(AtomicBool::new(false));
        let stop = stopped.clone();
        let worker = thread::spawn(move || {
            serve(
                &listener,
                &Arc::new(config),
                scripts,
                &stop,
                fragment_delay_ms,
            )
        });
        Ok(Self {
            port,
            root_der,
            stopped,
            worker: Some(worker),
        })
    }

    #[must_use]
    pub fn port(&self) -> u16 {
        self.port
    }

    #[must_use]
    pub fn root_der(&self) -> Vec<u8> {
        self.root_der.clone()
    }

    /// Join the bounded script and return received HTTP requests, never TLS private material.
    /// # Errors
    /// Reports fixture framing, stop/deadline, peer I/O or thread errors.
    pub fn finish(mut self) -> HttpsResult<Vec<String>> {
        self.worker
            .take()
            .ok_or(HttpsError::Fixture)?
            .join()
            .map_err(|_| HttpsError::Fixture)?
    }
}

impl Drop for FixtureServer {
    fn drop(&mut self) {
        self.stopped.store(true, Ordering::Release);
        if let Some(worker) = self.worker.take() {
            let _ = worker.join();
        }
    }
}

fn serve(
    listener: &TcpListener,
    config: &Arc<ServerConfig>,
    scripts: Vec<ResponseScript>,
    stop: &AtomicBool,
    fragment_delay_ms: u64,
) -> HttpsResult<Vec<String>> {
    let mut requests = Vec::new();
    for script in scripts {
        // Offline restart audits hash the executable and retained Raw before the
        // next connection; the server wait is bounded independently of any lease.
        let deadline = Deadline::new(60_000)?;
        let socket = loop {
            if stop.load(Ordering::Acquire) {
                return Err(HttpsError::Fixture);
            }
            deadline.remaining()?;
            match listener.accept() {
                Ok((socket, peer)) => {
                    if !peer.ip().is_loopback() {
                        return Err(HttpsError::Fixture);
                    }
                    break socket;
                }
                Err(e) if e.kind() == io::ErrorKind::WouldBlock => {
                    thread::sleep(Duration::from_millis(1));
                }
                Err(e) => return Err(e.into()),
            }
        };
        let connection = ServerConnection::new(config.clone())?;
        let mut stream = StreamOwned::new(connection, DeadlineTcp { socket, deadline });
        let mut request = Vec::new();
        while !request.ends_with(b"\r\n\r\n") {
            if request.len() >= MAX_HEADER_BYTES {
                return Err(HttpsError::Fixture);
            }
            let mut byte = [0];
            if stream.read(&mut byte)? == 0 {
                return Err(HttpsError::Truncated);
            }
            request.push(byte[0]);
        }
        requests.push(String::from_utf8(request).map_err(|_| HttpsError::Fixture)?);
        thread::sleep(Duration::from_millis(script.delay_ms));
        stream.write_all(&script.header)?;
        for fragment in script.body.chunks(script.fragment_bytes) {
            if fragment_delay_ms != 0 {
                thread::sleep(Duration::from_millis(fragment_delay_ms));
            }
            stream.write_all(fragment)?;
            stream.flush()?;
        }
        if script.close_notify {
            stream.conn.send_close_notify();
        }
        stream.flush()?;
    }
    Ok(requests)
}
