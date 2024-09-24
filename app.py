from fastapi import FastAPI, Form, Request, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
import paramiko
import pandas as pd
import os
import logging
import ipaddress
from concurrent.futures import ThreadPoolExecutor
from ubiquiti_password_changer import change_ubiquiti_password

# Initialize FastAPI app
app = FastAPI()

# Secret key for session middleware
app.add_middleware(SessionMiddleware, secret_key="AQYEV2781BUD821")

# Directory for templates and static files
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

#credentials for login (replace with more secure ones if needed)
VALID_USERNAME = "admin"
VALID_PASSWORD = "password"

# Set up logging
logging.basicConfig(filename='password_change.log', level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')


def is_logged_in(request: Request):
    return request.session.get("user") is not None


def change_password(host, username, port, password, profile_name, new_pass):
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        ssh.connect(host, port=port, username=username, password=password)
        logging.info(f"Connected to {host}")

        command = f"/user set numbers={profile_name} password={new_pass}"
        stdin, stdout, stderr = ssh.exec_command(command)

        output = stdout.read().decode()
        error_output = stderr.read().decode()

        if error_output:
            logging.error(f"Error on {host}: {error_output}")
            with open('failed_changes.txt', 'a') as fail_file:  # Log failed changes
                fail_file.write(f"{host} - Error: {error_output}\n")
            return f"Error on {host}: {error_output}"
        else:
            logging.info(f"Password for '{profile_name}' on {host} has been changed to '{new_pass}' successfully.")
            with open('success_changes.txt', 'a') as success_file:  # Log successful changes
                success_file.write(f"{host} - Password changed successfully\n")
            return f"Password for '{profile_name}' on {host} has been changed to '{new_pass}' successfully."

    except Exception as e:
        logging.error(f"Failed to change password on {host}: {e}")
        with open('failed_changes.txt', 'a') as fail_file:  # Log failed changes
            fail_file.write(f"{host} - Error: {str(e)}\n")
        return f"Failed to change password on {host}: {e}"

    finally:
        ssh.close()


def get_ips_from_subnet(subnet):
    try:
        net = ipaddress.ip_network(subnet, strict=False)
        return [str(ip) for ip in net.hosts()]  # List of all usable IP addresses in the subnet
    except ValueError as e:
        return [f"Invalid subnet: {str(e)}"]


def change_passwords_concurrently(ip_list, username, password, profile_name, new_pass, max_workers=1000):
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(change_password, ip, username, 8222, password, profile_name, new_pass) for ip in ip_list]
        results = [f.result() for f in futures]  # Get results from all threads
    return results


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})


@app.post("/login", response_class=HTMLResponse)
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    if username == VALID_USERNAME and password == VALID_PASSWORD:
        # Store the user in the session
        request.session["user"] = username
        return RedirectResponse(url="/", status_code=303)
    else:
        # Invalid credentials
        return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid username or password"})
    
    #index file app route
 
@app.get("/", response_class=HTMLResponse)
async def read_item(request: Request):
    if not is_logged_in(request):
        return RedirectResponse(url="/login")
    return templates.TemplateResponse("home.html", {"request": request})


@app.post("/manual_change/")
async def manual_change(request: Request, ip_address: str = Form(...), username: str = Form(...),
                        password: str = Form(...), profile_name: str = Form(...), new_pass: str = Form(...)):

    if not is_logged_in(request):
        return RedirectResponse(url="/login")

    # Detect if the input is a subnet
    if "/" in ip_address:
        ip_list = get_ips_from_subnet(ip_address)
        # Use concurrency to change passwords faster
        results = change_passwords_concurrently(ip_list, username, password, profile_name, new_pass)
        result = "\n".join(results)
    else:
        result = change_password(ip_address, username, 8222, password, profile_name, new_pass)

    return templates.TemplateResponse("index.html", {"request": request, "result": result})


@app.post("/upload_change/")
async def upload_change(request: Request, file: UploadFile = File(...), username: str = Form(...),
                        password: str = Form(...), profile_name: str = Form(...), new_pass: str = Form(...)):

    if not is_logged_in(request):
        return RedirectResponse(url="/login")

    try:
        # Save the uploaded Excel file
        file_location = f"uploads/{file.filename}"
        with open(file_location, "wb") as f:
            f.write(file.file.read())

        # Load the Excel file
        df = pd.read_excel(file_location)

        results = []
        for index, row in df.iterrows():
            host = row['IP_ADDRESS']  # Assuming the Excel has a column 'IP_ADDRESS'

            # Check if it's a subnet
            if "/" in host:
                ip_list = get_ips_from_subnet(host)
                # Use concurrency for subnet ranges
                subnet_results = change_passwords_concurrently(ip_list, username, password, profile_name, new_pass)
                results.extend(subnet_results)
            else:
                result = change_password(host, username, 8222, password, profile_name, new_pass)
                results.append(result)

        # Remove the file after processing
        os.remove(file_location)

        return templates.TemplateResponse("index.html", {"request": request, "result": "\n".join(results)})

    except Exception as e:
        return templates.TemplateResponse("index.html", {"request": request, "result": f"Error processing file: {str(e)}"})


@app.get("/logs", response_class=HTMLResponse)
async def show_logs(request: Request):
    if not is_logged_in(request):
        return RedirectResponse(url="/login")

    log_file = "password_change.log"

    if os.path.exists(log_file):
        with open(log_file, "r") as f:
            logs = f.read()
    else:
        logs = "No logs found."

    return templates.TemplateResponse("logs.html", {"request": request, "logs": logs})


# This is the Excel download button to download logs.
@app.get("/download_logs/")
async def download_logs():
    # Read logs from text files
    success_log = []
    fail_log = []

    # Load successful changes
    if os.path.exists('success_changes.txt'):
        with open('success_changes.txt', 'r') as f:
            success_log = f.readlines()

    # Load failed changes
    if os.path.exists('failed_changes.txt'):
        with open('failed_changes.txt', 'r') as f:
            fail_log = f.readlines()

    # Prepare the data for Excel
    data = {
        'Success Log': success_log,
        'Failed Log': fail_log,
    }

    # Create a DataFrame
    df = pd.DataFrame(dict([(k, pd.Series(v)) for k, v in data.items()]))

    # Save DataFrame to Excel
    output_file = 'password_change_log.xlsx'
    df.to_excel(output_file, index=False)

    # Return the Excel file as a response
    return FileResponse(output_file, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', filename=output_file)

@app.get("/index", response_class=HTMLResponse)
async def index_page(request: Request):
    if not is_logged_in(request):
        return RedirectResponse(url="/login")
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/logout")
async def logout(request: Request):
    # Clear the session
    request.session.clear()
    # Redirect to login page
    return RedirectResponse(url="/login", status_code=303)


# Route for displaying Ubiquiti form
@app.get("/ubiquiti_form", response_class=HTMLResponse)
async def ubiquiti_form(request: Request):
    if not is_logged_in(request):
        return RedirectResponse(url="/login")
    return templates.TemplateResponse("ubiquiti_form.html", {"request": request})


# Route for processing Ubiquiti password change (with verification)
@app.post("/ubiquiti_change/", response_class=HTMLResponse)
async def ubiquiti_change(request: Request, ip_address: str = Form(...), username: str = Form(...),
                          password: str = Form(...), new_pass: str = Form(...), verify_pass: str = Form(...)):

    if not is_logged_in(request):
        return RedirectResponse(url="/login")

    # Call the Ubiquiti password change function with verify_pass included
    result = change_ubiquiti_password(ip_address, username, 8222, password, new_pass, verify_pass)

    # Render the same form with the result message
    return templates.TemplateResponse("ubiquiti_form.html", {"request": request, "result": result})


LOG_FILE = "ubiquiti_password_change.log"

# Route to serve the radiologs page
@app.get("/radiologs", response_class=HTMLResponse)
async def radio_logs(request: Request):
    if not os.path.exists(LOG_FILE):
        logs = "No logs found."
    else:
        with open(LOG_FILE, "r") as f:
            logs = f.read()

    return templates.TemplateResponse("radiologs.html", {"request": request, "logs": logs})

# Route to download logs as Excel
@app.get("/download_radio_logs")
async def download_radio_logs():
    if os.path.exists(LOG_FILE):
        # Read logs and split successful and failed ones
        with open(LOG_FILE, "r") as f:
            log_lines = f.readlines()

        # Process logs and categorize
        successful_logs = [line for line in log_lines if "has been changed" in line]
        failed_logs = [line for line in log_lines if "Failed" in line]

        # Create a DataFrame
        df_success = pd.DataFrame(successful_logs, columns=["Success Logs"])
        df_failed = pd.DataFrame(failed_logs, columns=["Failed Logs"])

        # Save the DataFrame to an Excel file
        excel_file = "radio_logs.xlsx"
        with pd.ExcelWriter(excel_file) as writer:
            df_success.to_excel(writer, sheet_name="Successful", index=False)
            df_failed.to_excel(writer, sheet_name="Failed", index=False)

        return FileResponse(excel_file, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', filename='radio_logs.xlsx')

    else:
        return {"error": "Log file not found."}


