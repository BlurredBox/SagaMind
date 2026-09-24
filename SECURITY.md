# Security Policy

This document outlines the security policy for **SagaMind**, detailing how to report vulnerabilities, supported versions, and our commitment to maintaining a secure runtime environment for autonomous cognitive agents.

---

## 1. Supported Versions

We actively monitor and patch vulnerabilities in the following versions of SagaMind:

| Version | Supported | Security Patches |
| :--- | :--- | :--- |
| 1.0.x | Yes | Yes (Active) |
| < 1.0.0 | No | No (EOL) |

---

## 2. Reporting a Vulnerability

We take the security of our runtime and sandboxing layers seriously. If you discover a security vulnerability, **please do not disclose it publicly or open a public GitHub issue**. Instead, report it privately through one of the following methods to ensure a coordinated vulnerability disclosure (CVD) process.

### Contact Information
* **Email**: kesablyanharut@gmail.com
* **Subject Prefix**: `[SECURITY VULNERABILITY] SagaMind`

### What to Include in the Report
To help us triage and resolve the issue quickly, please include:
1. **Description**: A detailed description of the vulnerability and its potential impact.
2. **Steps to Reproduce**: A step-by-step guide or proof-of-concept (PoC) script demonstrating the exploit (e.g. bypass rules for path traversal or sandboxed escape payloads).
3. **System Environment**: Details about the operating system, container runtime configuration, and installed library versions (e.g., Python, Z3-solver, Wasmtime).
4. **Proposed Mitigation**: If you have identified a potential fix, code contributions or architecture adjustments are welcome.

---

## 3. Vulnerability Response and Triage Process

Upon receiving a report, the project maintainers will follow this protocol:

1. **Acknowledgment**: We will acknowledge receipt of your report within **48 hours**.
2. **Triage**: We will assess the severity (using CVSS v3.1 scoring) and verify the exploit in a secure sandbox.
3. **Remediation**: If confirmed, we will develop a patch. We aim to complete development and testing of security patches within **14 days** of verification.
4. **Advisory & Release**: We will publish a Security Advisory alongside a minor patch release. If the issue affects critical enterprise environments, we will coordinate with affected downstreams before public disclosure.

---

## 4. Sandboxing & Verification Security Architecture

SagaMind enforces a multi-layered security boundary to prevent malicious or stochastic agent actions from compromising host environments:

*   **Typed Policy Gate**: Built-in mutating tools cannot register without a typed argument policy. Unsupported, nested, extra, or path-escaping arguments fail closed. Optional SMT-LIB2 adds bounded checks for supported concrete parameters.
*   **Built-in Worker Boundary**: Trusted reference handlers run in short-lived processes with explicit filesystem, network, environment, time/CPU, memory, and output capabilities. Production forbids host fallback. This worker is not a container or hostile-code microVM.
*   **Wasmtime Isolation Layer**: Untrusted compiled tools can use a fuel-metered WASI boundary with explicit preopens and environment exposure. Network access is denied by the supported WASI path.
*   **Side-effect-free Speculative Validation**: Drafts are checked concurrently without executing their tool effects. Only the chosen valid draft is executed. Copy-on-write filesystem overlays are not implemented.

---

## 5. Disclosure Policy

We follow a coordinated disclosure model. We ask that security researchers keep reports confidential and allow a **90-day window** for remediation before making any details public, unless mutually agreed otherwise. We do not support or participate in public disclosures without coordinated vendor patches.
