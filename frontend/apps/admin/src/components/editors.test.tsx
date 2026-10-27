import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { IndustryEditorPage } from "@/components/industry/industry-editor";
import { WhatsAppEditor } from "@/components/whatsapp/wa-editor";
import { industryTemplate, REFERENCE, waTemplate, waVersion } from "@/test/fixtures";
import { authState, mockApi, navigation, renderWithQuery } from "@/test/utils";

const SA = { method: "GET", path: "/api/auth/me", body: authState() };
const SUPPORT = { method: "GET", path: "/api/auth/me", body: authState({ platform_role: "support" }) };
const VARIABLES = { method: "GET", path: "/api/admin/whatsapp-templates/variables", body: { variables: ["agency_name", "booking_reference", "customer_name", "scheduled_date"] } };
const SIBLINGS = { method: "GET", path: "/api/admin/whatsapp-templates", body: { count: 0, results: [] } };
const reference = (devControls = false) => ({ method: "GET", path: "/api/admin/reference-data", body: { ...REFERENCE, whatsapp_dev_controls: devControls } });

beforeEach(() => {
  navigation.pathname = "/whatsapp-templates/wt1";
  navigation.search = new URLSearchParams();
});

function renderWa(template: ReturnType<typeof waTemplate>, me = SA, extra: object[] = [], devControls = false) {
  const calls = mockApi([
    me,
    reference(devControls),
    VARIABLES,
    SIBLINGS,
    { method: "GET", path: "/api/admin/whatsapp-templates/wt1", body: template },
    ...(extra as never[]),
  ]);
  renderWithQuery(<WhatsAppEditor id="wt1" />);
  return calls;
}

describe("WhatsApp template editor (spec 2.7)", () => {
  it("shows the Workflow 3.4 status, live counters and the preview with samples", async () => {
    renderWa(waTemplate());

    const steps = await screen.findByRole("navigation", { name: "Approval status" });
    expect(within(steps).getByText("Draft").closest("li")).toHaveAttribute("aria-current", "step");
    expect(screen.getByText(`${waVersion().body.length}/1024`)).toBeInTheDocument();
    const preview = screen.getByRole("figure", { name: "Preview" });
    expect(within(preview).getByText("Asha")).toBeInTheDocument();
    expect(within(preview).getByText("28 Oct 2026")).toBeInTheDocument();

    const footer = screen.getByLabelText("Footer (optional)");
    fireEvent.change(footer, { target: { value: "x".repeat(61) } });
    expect(screen.getByText("61/60")).toHaveClass("text-red-600");
  });

  it("inserts a variable at the cursor and asks for its sample value", async () => {
    renderWa(waTemplate());
    const body = (await screen.findByLabelText("Body")) as HTMLTextAreaElement;
    await userEvent.click(body);
    body.setSelectionRange(body.value.length, body.value.length);
    await userEvent.click(screen.getByRole("button", { name: "{{booking_reference}}" }));

    expect(body.value.endsWith("{{booking_reference}}")).toBe(true);
    expect(screen.getByLabelText(/Sample values.*booking_reference/)).toBeInTheDocument();
    const preview = screen.getByRole("figure", { name: "Preview" });
    expect(within(preview).getByText("{{booking_reference}}")).toHaveClass("bg-amber-200"); // no sample yet
  });

  it("saves the draft; sending waits until changes are saved", async () => {
    const calls = renderWa(waTemplate(), SA, [
      { method: "PATCH", path: "/api/admin/whatsapp-templates/wt1/versions/v1", body: waVersion({ footer: "Reply STOP to opt out." }) },
    ]);
    const footer = await screen.findByLabelText("Footer (optional)");
    await userEvent.clear(footer);
    await userEvent.type(footer, "Reply STOP to opt out.");

    expect(screen.getByRole("button", { name: "Send to client for approval" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Save draft" }));

    expect(await screen.findByText("Draft saved.")).toBeInTheDocument();
    expect(calls.find((c) => c.method === "PATCH")?.body).toMatchObject({ footer: "Reply STOP to opt out." });
  });

  it("shows server field errors under the field", async () => {
    renderWa(waTemplate(), SA, [
      {
        method: "PATCH",
        path: "/api/admin/whatsapp-templates/wt1/versions/v1",
        status: 400,
        body: { code: "validation_error", detail: "Please correct the highlighted fields.", fields: { footer: ["The footer can't contain variables."] } },
      },
    ]);
    const footer = await screen.findByLabelText("Footer (optional)");
    await userEvent.clear(footer);
    await userEvent.type(footer, "Call us");
    await userEvent.click(screen.getByRole("button", { name: "Save draft" }));
    expect(await screen.findByText("The footer can't contain variables.")).toBeInTheDocument();
  });

  it("hides super-admin actions from support staff", async () => {
    renderWa(waTemplate([waVersion({ status: "signed_off", available_actions: ["submit", "disable"] })]), SUPPORT);
    await screen.findByRole("navigation", { name: "Approval status" });
    expect(screen.queryByRole("button", { name: "Submit to Meta" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Disable" })).not.toBeInTheDocument();
  });

  it("shows the rejection reason and offers a new version", async () => {
    renderWa(
      waTemplate([waVersion({ status: "rejected", rejection_reason: "Promotional wording", provider_template_id: "stub-1", available_actions: ["disable", "new_version"] })]),
    );
    expect(await screen.findByText("Meta rejected this version")).toBeInTheDocument();
    expect(screen.getByText("Promotional wording")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "New version" })).toBeInTheDocument();
    expect(screen.getByLabelText("Body")).toBeDisabled();
  });

  it("shows the client's send-back comment", async () => {
    renderWa(
      waTemplate([
        waVersion({
          status: "sent_back",
          available_actions: ["edit", "disable"],
          events: [{ action: "client_send_back", from_status: "sent_to_client", to_status: "sent_back", actor_email: "owner@agency.test", note: "Add our phone number", created_at: "2026-10-27T10:00:00Z" }],
        }),
      ]),
    );
    expect(await screen.findByText("The client sent this version back")).toBeInTheDocument();
    expect(screen.getAllByText("Add our phone number").length).toBeGreaterThan(0);
  });

  it("records a manual sign-off with an evidence upload", async () => {
    const calls = renderWa(waTemplate([waVersion({ status: "sent_to_client", available_actions: ["manual_signoff", "disable"] })]), SA, [
      { method: "POST", path: "/api/admin/whatsapp-templates/wt1/versions/v1/manual-signoff", body: waVersion({ status: "signed_off" }) },
    ]);
    await userEvent.click(await screen.findByRole("button", { name: "Record client sign-off" }));
    const dialog = screen.getByRole("dialog", { name: "Record the client's sign-off" });
    await userEvent.upload(within(dialog).getByLabelText(/Choose a file/), new File(["%PDF-1.4"], "signoff.pdf", { type: "application/pdf" }));
    await userEvent.click(within(dialog).getByRole("button", { name: "Record sign-off" }));

    await waitFor(() => expect(calls.some((c) => c.path.endsWith("/manual-signoff"))).toBe(true));
    expect(String(calls.find((c) => c.path.endsWith("/manual-signoff"))?.body)).toContain('name="evidence"');
  });

  it("shows the development Simulate panel only when the API allows it", async () => {
    renderWa(waTemplate([waVersion({ status: "submitted", provider_template_id: "stub-1", available_actions: ["disable"] })]), SA, [], true);
    expect(await screen.findByRole("region", { name: "Development only: simulate" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Meta approves" })).toBeInTheDocument();
  });

  it("hides the Simulate panel otherwise", async () => {
    renderWa(waTemplate([waVersion({ status: "submitted", provider_template_id: "stub-1", available_actions: ["disable"] })]));
    await screen.findByRole("navigation", { name: "Approval status" });
    expect(screen.queryByRole("region", { name: "Development only: simulate" })).not.toBeInTheDocument();
  });
});

describe("Industry template editor (spec 2.6)", () => {
  function renderIndustry(me = SA, extra: object[] = [], clientsUsing = 3) {
    const calls = mockApi([
      me,
      { method: "GET", path: "/api/admin/industry-templates/it1", body: industryTemplate(clientsUsing) },
      ...(extra as never[]),
    ]);
    renderWithQuery(<IndustryEditorPage id="it1" />);
    return calls;
  }

  it("explains the in-use rules and locks what can't change", async () => {
    renderIndustry();
    expect(await screen.findByText("3 client(s) use this template")).toBeInTheDocument();
    expect(screen.getByLabelText("Type: booking_reference")).toBeDisabled(); // core
    expect(screen.getByLabelText("Type: quantity")).toBeDisabled(); // in use
    expect(screen.getByLabelText("Label: booking_reference")).toBeEnabled(); // labels can change

    await userEvent.click(screen.getAllByRole("button", { name: "Add field" })[1]!);
    expect(screen.getByLabelText("Required: 3")).toBeDisabled(); // new fields must be optional
  });

  it("saves the whole definition as a new version", async () => {
    const saved = { ...industryTemplate(), current_version: 3 };
    const calls = renderIndustry(SA, [{ method: "PUT", path: "/api/admin/industry-templates/it1", body: saved }]);
    await screen.findByText("3 client(s) use this template");
    await userEvent.click(screen.getAllByRole("button", { name: "Add field" })[1]!);
    await userEvent.type(screen.getByLabelText(/Cylinder delivery: Fields: Key 3/), "delivery_slot");
    await userEvent.type(screen.getByLabelText("Label: delivery_slot"), "Delivery slot");
    await userEvent.click(screen.getByRole("button", { name: "Save as new version" }));

    expect(await screen.findByText("Saved as version 3.")).toBeInTheDocument();
    const body = calls.find((c) => c.method === "PUT")?.body as { schedule_types: { fields: Record<string, unknown>[] }[] };
    const added = body.schedule_types[0]!.fields.at(-1)!;
    expect(added).toMatchObject({ key: "delivery_slot", label: "Delivery slot", required: false });
    expect(added).not.toHaveProperty("isNew");
  });

  it("shows Workflow 3.5 refusals from the server", async () => {
    renderIndustry(SA, [
      {
        method: "PUT",
        path: "/api/admin/industry-templates/it1",
        status: 409,
        body: { code: "required_field_in_use", detail: "'quantity' can't become required: clients already use this template.", fields: {} },
      },
    ]);
    await screen.findByText("3 client(s) use this template");
    await userEvent.click(screen.getByRole("button", { name: "Save as new version" }));
    expect(await screen.findByText(/can't become required: clients already use this template/)).toBeInTheDocument();
  });

  it("is read-only for support staff", async () => {
    renderIndustry(SUPPORT);
    await screen.findByText("3 client(s) use this template");
    expect(screen.queryByRole("button", { name: "Save as new version" })).not.toBeInTheDocument();
    expect(screen.getByLabelText("Label: booking_reference")).toBeDisabled();
  });
});
