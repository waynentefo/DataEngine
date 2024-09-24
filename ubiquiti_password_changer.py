import paramiko
import time
import logging
import os
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
import ipaddress

# Set up logging for Ubiquiti radio password changes
logging.basicConfig(filename='ubiquiti_password_change.log', level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')

app = FastAPI()

# Define templates directory
templates = Jinja2Templates(directory="templates")

# Change this to the number of threads you want to use concurrently
MAX_WORKERS = 1000

def change_ubiquiti_password(host, username, port, password, new_pass, verify_pass):
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        # Connect to Ubiquiti device
        ssh.connect(host, port=port, username=username, password=password)
        logging.info(f"Connected to {host}")

        # Open an interactive shell session
        shell = ssh.invoke_shell()

        # Wait for the shell to initialize
        time.sleep(1)

        # Read initial output (if any)
        output = shell.recv(1024).decode()
        logging.info(f"Initial Output: {output}")

        # Send the 'passwd' command
        shell.send('passwd\n')
        time.sleep(1)

        # Read the output to ensure the command was received
        output = shell.recv(1024).decode()
        logging.info(f"Received after 'passwd' command: {output}")

        if 'New password:' in output:
            # Send the new password
            shell.send(f'{new_pass}\n')
            time.sleep(1)

            # Read the output after sending new password
            output = shell.recv(1024).decode()
            logging.info(f"Received after new password: {output}")

            if 'Retype password:' in output:
                # Send the verified (retype) password
                shell.send(f'{verify_pass}\n')
                time.sleep(1)

                # Read the final output
                output = shell.recv(1024).decode()
                logging.info(f"Received after confirming new password: {output}")

                # Adjust the success condition to match the correct success message
                if 'Password for admin changed by admin' in output or 'password updated successfully' in output or 'Password changed' in output:
                    logging.info(f"Password on {host} has been changed to '{new_pass}' successfully.")
                    return f"Password on {host} has been changed to '{new_pass}' successfully."
                else:
                    logging.error(f"Failed to change password on {host}. Output: {output}")
                    return f"Failed to change password on {host}. Output: {output}"

        logging.error(f"Unexpected output on {host}: {output}")
        return f"Failed to change password on {host}. Unexpected output: {output}"

    except Exception as e:
        logging.error(f"Failed to change password on {host}: {e}")
        return f"Failed to change password on {host}: {e}"

    finally:
        ssh.close()

def process_ips(ips, username, port, password, new_pass, verify_pass):
    """
    Process a list of IPs concurrently to change the password.
    """
    results = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(change_ubiquiti_password, str(ip), username, port, password, new_pass, verify_pass): ip for ip in ips}
        
        for future in as_completed(futures):
            ip = futures[future]
            try:
                result = future.result()
                results.append(result)
            except Exception as e:
                results.append(f"Failed to change password for {ip}: {e}")
    
    return results

@app.post("/ubiquiti_change/")
async def ubiquiti_change(request: Request):
    form = await request.form()
    ip_range = form.get("ip_address")  # Can be a single IP or a subnet
    username = form.get("username")
    password = form.get("password")
    new_pass = form.get("new_pass")
    verify_pass = form.get("verify_pass")
    port = 22  # Default SSH port; modify as needed

    try:
        # Use ipaddress module to handle both single IPs and subnets
        ip_network = ipaddress.ip_network(ip_range, strict=False)
        ips = ip_network.hosts()  # This generates all usable IP addresses in the subnet

        # Change password concurrently for all IPs
        results = process_ips(ips, username, port, password, new_pass, verify_pass)

        # Display results in the template (this could be a success message or error logs)
        return templates.TemplateResponse("result.html", {"request": request, "results": results})

    except ValueError as e:
        # Handle invalid IP range or format
        logging.error(f"Invalid IP address or subnet: {e}")
        return templates.TemplateResponse("result.html", {"request": request, "results": [f"Invalid IP or subnet: {e}"]})

