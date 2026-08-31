# Novatek Form Automation

The **Novatek Form Automation** protocol defines the automated field handling, question input rules, file upload procedures, and submission verification for the **"Fill current form"** workflow in the [[Novatek Web Application]] (`https://nec-dev.autotrial.app`).

## Core Operational Principles

Whenever navigating or processing forms in Novatek, follow these three mandatory principles:

1. **Checkmark & Status Verification**:
   - Whenever a form is selected, inspect the right side / status indicators for a completion checkmark.
   - If it is **not marked complete**, proceed to fill and submit it.
2. **Comprehensive Scrolling & Completion**:
   - Scroll down through the entire form (`browser_scroll`) to ensure every question is visible and answered properly according to established rules (first options, standard inputs, timestamps, signatures, file uploads).
   - If below-the-fold questions or the Submit button are not yet visible, keep scrolling down the form card and answering newly exposed questions until the bottom is reached.
3. **Submission & Verification**:
   - Locate and click the **Submit** button at the bottom of the page.
   - Verify that the top-right success banner confirms completion with status **Success**.

## Question Types & Automated Input Specifications

When the user commands JARVIS to **"Fill current form"** (or "fill form in Novatek", "complete current form"), apply the following rules per question type:

### 1. Single Choice Question
- **Action**: Always select the **first choice on the left** (first radio option / widget button).

### 2. Multiple Choice Question (Checkboxes)
- **Action**: Always check the **first checkbox** in the options list.

### 3. Date Question
- **Action**: Can be answered either by:
  - Clicking on the **calendar icon on the right side of the input field** and selecting the current day, OR
  - Typing the current day directly into the input field in **`M/D/YYYY`** format (e.g. `8/29/2026`).

### 4. Time Question (12-Hour / 24-Hour Format)
- **Action**: Always enter the **current time** and verify/select the **24-hour format**.

### 5. Text Question
- **Action**: Always enter the string `"test"`.

### 6. Number Question
- **Action**: Always enter the number `55`.

### 7. Question with Unit
- **Action**: Enter `55` in the numeric input field. Open the unit dropdown menu directly to the right of the numeric field and select the **first item from the dropdown**.

### 8. File Upload Question
- **Action**:
  1. Click on the file upload target/button to trigger the file selection dialog (drag & drop is not supported).
  2. Navigate to and select the consent template file: `/home/proslayer/AndroidStudioProjects/jarvis_full/Informed_Consent Template.pdf` (via `browser_upload_file`).
  3. When the calendar picker appears after file selection, select **today's / now date**.
  4. When prompted for time, select the **current time**.

### 9. Signature Question
- **Action**:
  1. Click the **Sign** button situated on the right side of the question card.
  2. In the signature verification dialog that opens, enter the credentials from [[Novatek Web Application]]:
     - **Username**: from `novatek.username` in `config.json` (or `NOVATEK_USERNAME`)
     - **Password**: from `novatek.password` in `config.json` (or `NOVATEK_PASSWORD`)
  3. Submit / confirm the signature dialog.

### 10. Form Submission & Success Verification
- **Action**:
  1. Locate and click the **Submit** button at the bottom/end of the form.
  2. Verify that the form was submitted successfully by checking for the success toast/notification banner appearing on the **top right side** of the screen with status **Success**.

## Related Systems

- [[Novatek Web Application]] for application architecture, portal access, and admin credentials.
- [[Playwright Browser Control]] for executing Flutter canvas inputs, widget clicks, file uploads, and screenshot captures.
- [[Zen White Glassmorphic UI]] for live progress monitoring and visual feedback.
- [[Linux System Controller]] for underlying OS and local file management.
