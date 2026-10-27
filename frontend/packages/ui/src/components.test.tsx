import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { Button } from "./button";
import { DataTable } from "./data-table";
import { ConfirmDialog, ReasonDialog } from "./dialog";
import { TextField } from "./fields";
import { FileUpload } from "./file-upload";
import { StatusBadge } from "./status-pill";

describe("StatusBadge", () => {
  it.each([
    ["active", "ok"],
    ["suspended", "error"],
    ["sent_to_client", "warn"],
    ["submitted", "info"],
    ["something_new", "neutral"],
  ])("%s is shown as %s", (status, tone) => {
    render(<StatusBadge status={status} label="Label" />);
    expect(screen.getByText("Label").closest("[data-tone]")).toHaveAttribute("data-tone", tone);
  });
});

describe("TextField", () => {
  it("links its label and shows an error accessibly", () => {
    render(<TextField label="Email" error="Enter a valid email" required />);
    const input = screen.getByLabelText(/Email/);
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(input).toHaveAccessibleDescription("Enter a valid email");
  });
});

describe("Button", () => {
  it("is disabled while loading", () => {
    render(<Button loading>Save</Button>);
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });
});

describe("DataTable", () => {
  const columns = [{ key: "name", header: "Name", render: (row: { name: string }) => row.name }];

  it("renders rows and reports clicks", async () => {
    const onRowClick = vi.fn();
    render(
      <DataTable
        columns={columns}
        rows={[{ name: "Kochi Gas" }, { name: "Thrissur Gas" }]}
        rowKey={(row) => row.name}
        empty="No clients"
        onRowClick={onRowClick}
      />,
    );
    expect(screen.getByRole("columnheader", { name: "Name" })).toBeInTheDocument();
    await userEvent.click(screen.getByText("Thrissur Gas"));
    expect(onRowClick).toHaveBeenCalledWith({ name: "Thrissur Gas" });
  });

  it("shows the empty state and the loading state", () => {
    const { rerender } = render(
      <DataTable columns={columns} rows={[]} rowKey={(r) => r.name} empty="No clients yet" />,
    );
    expect(screen.getByText("No clients yet")).toBeInTheDocument();
    rerender(
      <DataTable columns={columns} rows={undefined} rowKey={(r) => r.name} empty="x" loading loadingLabel="Loading" />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("Loading");
  });
});

describe("ConfirmDialog", () => {
  it("confirms, cancels and closes on Escape", async () => {
    const onConfirm = vi.fn();
    const onClose = vi.fn();
    render(
      <ConfirmDialog
        open
        title="End support session?"
        message="You will leave the client's account."
        confirmLabel="End session"
        cancelLabel="Cancel"
        onConfirm={onConfirm}
        onClose={onClose}
      />,
    );
    const dialog = screen.getByRole("dialog", { name: "End support session?" });
    await userEvent.click(within(dialog).getByRole("button", { name: "End session" }));
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await userEvent.keyboard("{Escape}");
    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("renders nothing when closed", () => {
    render(
      <ConfirmDialog open={false} title="t" message="m" confirmLabel="ok" cancelLabel="no" onConfirm={vi.fn()} onClose={vi.fn()} />,
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

describe("ReasonDialog", () => {
  function setup() {
    const onSubmit = vi.fn();
    render(
      <ReasonDialog
        open
        title="Suspend client"
        label="Reason"
        confirmLabel="Suspend"
        cancelLabel="Cancel"
        messages={{ tooShort: "At least 5 characters.", tooLong: "At most 500 characters." }}
        onSubmit={onSubmit}
        onClose={vi.fn()}
      />,
    );
    return onSubmit;
  }

  it("requires 5 to 500 characters", async () => {
    const onSubmit = setup();
    await userEvent.type(screen.getByLabelText(/Reason/), "late");
    await userEvent.click(screen.getByRole("button", { name: "Suspend" }));
    expect(await screen.findByText("At least 5 characters.")).toBeInTheDocument();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("submits the trimmed reason", async () => {
    const onSubmit = setup();
    await userEvent.type(screen.getByLabelText(/Reason/), "  Payment overdue  ");
    await userEvent.click(screen.getByRole("button", { name: "Suspend" }));
    await vi.waitFor(() => expect(onSubmit).toHaveBeenCalledWith("Payment overdue"));
  });

  it("puts focus in the reason box when it opens", () => {
    setup();
    expect(screen.getByLabelText(/Reason/)).toHaveFocus();
  });
});

describe("FileUpload", () => {
  const messages = {
    wrongType: "Upload a PDF, PNG or JPG file.",
    tooLarge: "The file must be 5 MB or smaller.",
    choose: "Choose a file",
    replace: "Replace",
  };

  function setup() {
    const onChange = vi.fn();
    render(
      <FileUpload
        label="Sign-off evidence"
        accept={[".pdf", ".png", ".jpg", ".jpeg"]}
        maxBytes={5 * 1024 * 1024}
        messages={messages}
        onChange={onChange}
      />,
    );
    return { onChange, input: screen.getByLabelText(/Choose a file/) as HTMLInputElement };
  }

  it("accepts an allowed file", async () => {
    const { onChange, input } = setup();
    const file = new File(["%PDF-1.4"], "signoff.pdf", { type: "application/pdf" });
    await userEvent.upload(input, file);
    expect(onChange).toHaveBeenCalledWith(file);
    expect(screen.getByText("signoff.pdf")).toBeInTheDocument();
  });

  it("rejects the wrong type and files over the size limit", async () => {
    const { onChange, input } = setup();
    await userEvent.upload(input, new File(["MZ"], "tool.exe"), { applyAccept: false });
    expect(screen.getByText(messages.wrongType)).toBeInTheDocument();

    const big = new File([new Uint8Array(5 * 1024 * 1024 + 1)], "big.pdf");
    await userEvent.upload(input, big);
    expect(screen.getByText(messages.tooLarge)).toBeInTheDocument();
    expect(onChange).toHaveBeenLastCalledWith(null);
  });
});
