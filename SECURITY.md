# Security Policy

## Reporting a Vulnerability

**Please do not report security vulnerabilities through public GitHub issues.**

If you believe you have found a security vulnerability in tt-auto-triage, please report it to us through coordinated disclosure.

### Preferred Method: GitHub Private Vulnerability Reporting

We use GitHub's built-in private vulnerability reporting feature. To report a vulnerability:

1. Navigate to the **Security** tab of this repository
2. Click **Report a vulnerability**
3. Fill in the details of the vulnerability
4. Submit the report

This ensures your report is received securely and privately by the maintainers.

### Alternative Method: Email

If you prefer, you can also report vulnerabilities via email to:

**ospo@tenstorrent.com**

Please include:
- A description of the vulnerability
- Steps to reproduce the issue
- Potential impact
- Any suggested fixes (if available)

### What to Expect

- **Acknowledgment**: We will acknowledge receipt of your report within 48 hours
- **Updates**: We will keep you informed about our progress addressing the vulnerability
- **Disclosure**: We ask that you do not publicly disclose the vulnerability until we have had a chance to address it
- **Credit**: We will credit you for the discovery when we publish the fix (unless you prefer to remain anonymous)

## Supported Versions

We release patches for security vulnerabilities for the following versions:

| Version | Supported          |
| ------- | ------------------ |
| main    | :white_check_mark: |

## Scope

This security policy applies to:
- The tt-auto-triage GitHub Actions and workflows
- The Python scripts used for analysis and triage
- Any credentials or secrets handling mechanisms

Security issues related to dependencies should first be reported to the respective dependency maintainers, then to us if they affect tt-auto-triage.

## Out of Scope

The following are generally not considered security vulnerabilities:
- Issues in third-party dependencies (report to the dependency maintainers)
- Theoretical vulnerabilities without proof of concept
- Social engineering attacks

Thank you for helping keep tt-auto-triage and our users safe!
