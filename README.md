# 📩 Telegram Auto-Link Bot

A production-ready Telegram bot that manages multiple channels and automatically rotates their invite links on a configurable schedule. Built with **aiogram 3**, **MongoDB (Motor)**, and **APScheduler**, and designed to run continuously on **Koyeb**.

---

## Table of Contents

1. [Features](#-features)
2. [Architecture](#-architecture)
3. [Project Structure](#-project-structure)
4. [Prerequisites](#-prerequisites)
5. [Installation (Local)](#-installation-local)
6. [Deployment on Koyeb](#-deployment-on-koyeb)
7. [Environment Variables](#-environment-variables)
8. [Database Schema](#-database-schema)
9. [How Link Rotation Works](#-how-link-rotation-works)
10. [Command Reference](#-command-reference)
11. [Permissions Matrix](#-permissions-matrix)
12. [Usage Walkthrough](#-usage-walkthrough)
13. [Testing Guide](#-testing-guide)
14. [Troubleshooting](#-troubleshooting)
15. [Security Notes](#-security-notes)
16. [License & Legal Use](#-license--legal-use)

---

## ✨ Features

- **Multi-channel management** — one bot instance serves unlimited channels.
- **Multi-user isolation** — each user sees only their own channels and links.
- **Automatic invite-link rotation** on 5m / 10m / 1h / 24h / custom intervals.
- **Role-based access control** — Owner, Admin, User.
- **Persistent storage** — all data survives restarts and redeploys.
- **Crash-safe scheduler** — jobs are rebuilt from MongoDB on every startup, so no duplicates and no losses.
- **Owner dashboard** — user count, channel count, scheduled job count, recent activity.
- **Health-check endpoint** (`/health`) for Koyeb and other PaaS providers.
- **Full English UI** — every message, button, and log line is English-only.

---

## 🏗 Architecture
