import streamlit as st
import pandas as pd
import os, sys, subprocess

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.dirname(BASE))
# Auto-detect OS — works on both Windows and Ubuntu without config dependency
OS = "ubuntu" if sys.platform.startswith("linux") else "windows"
XML_DIR = os.path.join(ROOT, "deploy", "legacy", "scheduled_tasks", "PairTrading")

TASK_NAMES = [
    "Hourly Scan",
    "Monitor",
]

XML_MAP = {
    "Hourly Scan": "PairTrading_Hourly_Scan.xml",
    "Monitor": "PairTrading_Monitor.xml",
}

FOLDER = "PairTrading"


def _run_schtasks(args):
    cmd = f'schtasks {args}'
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, shell=True)
        return r.returncode == 0, r.stdout.strip() or r.stderr.strip()
    except Exception as e:
        return False, str(e)


def get_task_info(name):
    ok, out = _run_schtasks(f'/Query /TN "{FOLDER}\\{name}" /FO LIST /V')
    if not ok:
        return None
    info = {}
    for line in out.splitlines():
        line = line.strip()
        if ':' in line:
            k, v = line.split(':', 1)
            info[k.strip()] = v.strip()
    return info


def _deploy(name):
    xml = XML_MAP.get(name)
    if not xml:
        return False, f"No XML mapping for {name}"
    xml_path = os.path.join(XML_DIR, xml)
    if not os.path.exists(xml_path):
        return False, f"XML file not found: {xml}"
    return _run_schtasks(f'/Create /XML "{xml_path}" /TN "{FOLDER}\\{name}" /F')


def show():
    if OS == "ubuntu":
        return _show_ubuntu()
    st.title("PairTrading — Task Scheduler")
    st.markdown("Deploy, enable, disable, run, or delete PairTrading Windows scheduled tasks from here.")

    tab1, tab2 = st.tabs(["Tasks", "Import / Export"])

    with tab1:
        rows = []
        for name in TASK_NAMES:
            info = get_task_info(name)
            if info is None:
                rows.append({"Task": name, "Status": "NOT FOUND",
                             "Last Run": "---", "Next Run": "---",
                             "Last Result": "---", "Enabled": "---"})
                continue
            rows.append({
                "Task": name,
                "Status": info.get("Status", "---"),
                "Last Run": info.get("Last Run Time", "---"),
                "Next Run": info.get("Next Run Time", "---"),
                "Last Result": info.get("Last Result", "---"),
                "Enabled": info.get("Scheduled Task State", "---"),
            })

        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, hide_index=True)

        st.divider()
        st.subheader("Task Controls")

        for name in TASK_NAMES:
            info = get_task_info(name)
            exists = info is not None
            state = info.get("Scheduled Task State", "Unknown") if info else "---"

            with st.container(border=True):
                st.markdown(f"**{name}**" + (" ✅ Deployed" if exists else " ❌ Not deployed"))

                if exists:
                    col1, col2, col3, col4, col5 = st.columns([1.2, 1.2, 0.9, 0.9, 0.9])
                    col1.markdown(f"Status: {info.get('Status', '---')}")
                    col2.markdown(f"Last: {info.get('Last Run Time', '---')}")
                    col3.markdown(f"Result: {info.get('Last Result', '---')}")

                    is_enabled = state == "Enabled"
                    btn_label = "Disable" if is_enabled else "Enable"
                    btn_type = "secondary" if is_enabled else "primary"

                    if col4.button(btn_label, key=f"toggle_{name}", type=btn_type):
                        action = "ENABLE" if not is_enabled else "DISABLE"
                        ok, msg = _run_schtasks(f'/Change /TN "{FOLDER}\\{name}" /{action}')
                        st.success(f"{name} {action}d") if ok else st.error(msg)
                        st.rerun()

                    if col5.button("Run", key=f"run_{name}"):
                        ok, msg = _run_schtasks(f'/Run /TN "{FOLDER}\\{name}"')
                        st.success(f"{name} triggered") if ok else st.error(msg)

                    run_col, kill_col, redeploy_col, delete_col = st.columns(4)
                    if run_col.button("Kill", key=f"end_{name}"):
                        ok, msg = _run_schtasks(f'/End /TN "{FOLDER}\\{name}"')
                        st.success(f"{name} stopped") if ok else st.error(msg)

                    if kill_col.button("Redeploy", key=f"redeploy_{name}"):
                        ok, msg = _deploy(name)
                        st.success(f"{name} redeployed") if ok else st.error(msg)
                        st.rerun()

                    if redeploy_col.button("Delete", key=f"delete_{name}"):
                        ok, msg = _run_schtasks(f'/Delete /TN "{FOLDER}\\{name}" /F')
                        st.success(f"{name} deleted") if ok else st.error(msg)
                        st.rerun()

                    with st.expander("Full details", expanded=False):
                        detail_df = pd.DataFrame(list(info.items()), columns=["Property", "Value"])
                        st.dataframe(detail_df, use_container_width=True, hide_index=True)
                else:
                    if st.button("Deploy", key=f"deploy_{name}", type="primary"):
                        ok, msg = _deploy(name)
                        st.success(f"{name} deployed") if ok else st.error(msg)
                        st.rerun()

    with tab2:
        st.subheader("Create Task from XML")
        xml_files = sorted([f for f in os.listdir(XML_DIR) if f.endswith(".xml")])
        if not xml_files:
            st.info("No XML files found in scheduled_tasks/")
        else:
            selected_xml = st.selectbox("Select XML file", xml_files, key="import_xml")
            xml_path = os.path.join(XML_DIR, selected_xml)

            if st.button("Import Task", type="primary"):
                task_name = selected_xml.replace(".xml", "").replace("_", " ").replace("-", " ").title()
                ok, msg = _run_schtasks(f'/Create /XML "{xml_path}" /TN "{FOLDER}\\{task_name}" /F')
                st.success(f"Task '{task_name}' created") if ok else st.error(msg)
                st.rerun()

            with open(xml_path, "rb") as f:
                raw = f.read()
            if raw[:2] == b'\xff\xfe':
                xml_content = raw.decode("utf-16-le")
            else:
                xml_content = raw.decode("utf-8", errors="replace")
            with st.expander("Preview XML"):
                st.code(xml_content[:2000], language="xml")

        st.divider()
        st.subheader("Task Logs")
        log_task = st.selectbox("Select task", TASK_NAMES, key="log_task")
        if st.button("Fetch Last 10 Events"):
            ok, out = _run_schtasks(f'/Query /TN "{FOLDER}\\{log_task}" /FO LIST /V')
            if ok:
                lines = out.splitlines()
                log_lines = [l for l in lines if 'Event' in l or 'Task Scheduler' in l or 'Event ID' in l]
                for l in log_lines[-20:]:
                    st.text(l.strip())
            else:
                st.error(out)


def _show_ubuntu():
    st.title("PairTrading — Task Scheduler")

    tab1, tab2 = st.tabs(["Scheduled Scans", "Logs"])

    with tab1:
        st.subheader("PairTrading Cron Jobs")
        cron_content = subprocess.run(["sudo", "-n", "cat", "/etc/cron.d/ngen26"], capture_output=True, text=True).stdout
        if cron_content:
            found = False
            for line in cron_content.split("\n"):
                if "PairTrading" in line or "scan_pairs" in line:
                    st.code(line, language="bash")
                    found = True
            if not found:
                st.info("No PairTrading cron jobs found")
        else:
            st.info("Cron file not found")

        st.divider()
        st.markdown("**Run Pair Scan Manually**")
        c1, c2 = st.columns([1, 3])
        if c1.button("Scan Pairs", type="primary"):
            cmd = ["/home/kiran/ngen26/venv/bin/python",
                   "PairTrading/live/scan_pairs.py"]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60, cwd="/home/kiran/ngen26")
            st.code((r.stdout + "\n" + r.stderr).strip()[:2000], language="bash")
        c2.markdown("Scans all active pairs for entry signals")

    with tab2:
        st.subheader("PairTrading Logs")
        log_file = "/home/kiran/logs/scan_pairs.log"
        lines = st.number_input("Lines", min_value=10, max_value=500, value=50, step=10)
        if st.button("Refresh", use_container_width=True):
            st.rerun()
        r = subprocess.run(["sudo", "-n", "tail", "-n", str(lines), log_file], capture_output=True, text=True)
        st.code(r.stdout if r.stdout.strip() else "No logs", language="bash")
