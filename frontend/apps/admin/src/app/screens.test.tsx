import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { ConsoleShell } from "@/components/console-shell";
import { authState, mockApi, navigation, renderWithQuery, router } from "@/test/utils";

import LoginPage from "./(auth)/login/page";
import TwoFactorPage from "./(auth)/two-factor/page";

beforeEach(() => {
  navigation.pathname = "/dashboard";
  navigation.search = new URLSearchParams();
});

describe("Login", () => {
  it("validates before calling the API", async () => {
    const calls = mockApi([{ method: "GET", path: "/api/auth/me", status: 403, body: {} }]);
    renderWithQuery(<LoginPage />);

    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByText("Enter a valid email address.")).toBeInTheDocument();
    expect(screen.getByText("This field is required.")).toBeInTheDocument();
    expect(calls.some((c) => c.path === "/api/auth/login")).toBe(false);
  });

  it("shows the server's error for wrong credentials", async () => {
    mockApi([
      { method: "GET", path: "/api/auth/me", status: 403, body: {} },
      {
        method: "POST",
        path: "/api/auth/login",
        status: 400,
        body: { code: "invalid_credentials", detail: "Invalid email or password.", fields: {} },
      },
    ]);
    renderWithQuery(<LoginPage />);

    await userEvent.type(screen.getByLabelText(/Email/), "admin@acube.test");
    await userEvent.type(screen.getByLabelText(/Password/), "wrong-password");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid email or password.");
    expect(router.replace).not.toHaveBeenCalled();
  });

  it("goes to two-factor after the password step, keeping ?next", async () => {
    navigation.search = new URLSearchParams("next=/clients");
    const calls = mockApi([
      { method: "GET", path: "/api/auth/me", status: 403, body: {} },
      {
        method: "POST",
        path: "/api/auth/login",
        body: authState({ otp_required: true, otp_verified: false }),
      },
    ]);
    renderWithQuery(<LoginPage />);

    await userEvent.type(screen.getByLabelText(/Email/), "admin@acube.test");
    await userEvent.type(screen.getByLabelText(/Password/), "super-secret-passphrase");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));

    await waitFor(() => expect(router.replace).toHaveBeenCalledWith("/two-factor?next=%2Fclients"));
    expect(calls.find((c) => c.path === "/api/auth/login")?.body).toEqual({
      email: "admin@acube.test",
      password: "super-secret-passphrase",
    });
  });

  it("refuses client users and signs them out again", async () => {
    const calls = mockApi([
      { method: "GET", path: "/api/auth/me", status: 403, body: {} },
      { method: "POST", path: "/api/auth/login", body: authState({ platform_role: "", requires_2fa: false }) },
      { method: "POST", path: "/api/auth/logout", status: 204 },
    ]);
    renderWithQuery(<LoginPage />);

    await userEvent.type(screen.getByLabelText(/Email/), "manager@agency.test");
    await userEvent.type(screen.getByLabelText(/Password/), "manager-password-1");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("This console is for the platform team");
    await waitFor(() => expect(calls.some((c) => c.path === "/api/auth/logout")).toBe(true));
  });
});

describe("Two-factor", () => {
  it("sets up an authenticator: shows the QR code and key, then confirms", async () => {
    const calls = mockApi([
      {
        method: "GET",
        path: "/api/auth/me",
        body: authState({ otp_setup_required: true, otp_device_confirmed: false, otp_verified: false }),
      },
      {
        method: "POST",
        path: "/api/auth/otp/setup",
        body: { otpauth_url: "otpauth://totp/SC:admin?secret=JBSWY3DPEHPK3PXP&issuer=SC", secret: "x" },
      },
      { method: "POST", path: "/api/auth/otp/confirm", body: authState() },
    ]);
    renderWithQuery(<TwoFactorPage />);

    expect(await screen.findByText("Set up two-factor authentication")).toBeInTheDocument();
    expect(await screen.findByText("JBSW Y3DP EHPK 3PXP")).toBeInTheDocument();
    expect(screen.getByTitle("QR code for your authenticator app")).toBeInTheDocument();

    await userEvent.type(screen.getByLabelText("6-digit code"), "123456");
    await userEvent.click(screen.getByRole("button", { name: "Confirm and continue" }));

    await waitFor(() => expect(router.replace).toHaveBeenCalledWith("/dashboard"));
    expect(calls.find((c) => c.path === "/api/auth/otp/confirm")?.body).toEqual({ otp_code: "123456" });
  });

  it("asks for a code when an authenticator exists and shows wrong codes", async () => {
    mockApi([
      { method: "GET", path: "/api/auth/me", body: authState({ otp_required: true, otp_verified: false }) },
      {
        method: "POST",
        path: "/api/auth/otp/verify",
        status: 400,
        body: { code: "invalid_otp", detail: "That code is not valid.", fields: {} },
      },
    ]);
    renderWithQuery(<TwoFactorPage />);

    expect(await screen.findByText("Two-factor authentication")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("6-digit code"), "000000");
    await userEvent.click(screen.getByRole("button", { name: "Verify" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("That code isn't valid");
  });

  it("sends signed-out visitors to sign in", async () => {
    mockApi([{ method: "GET", path: "/api/auth/me", status: 403, body: {} }]);
    renderWithQuery(<TwoFactorPage />);
    await waitFor(() => expect(router.replace).toHaveBeenCalledWith("/login"));
  });
});

describe("Console layout", () => {
  it("redirects signed-out users to sign in", async () => {
    mockApi([{ method: "GET", path: "/api/auth/me", status: 403, body: {} }]);
    renderWithQuery(<ConsoleShell>secret page</ConsoleShell>);

    await waitFor(() => expect(router.replace).toHaveBeenCalledWith("/login?next=%2Fdashboard"));
    expect(screen.queryByText("secret page")).not.toBeInTheDocument();
  });

  it("redirects users who haven't finished 2FA", async () => {
    mockApi([{ method: "GET", path: "/api/auth/me", body: authState({ otp_required: true, otp_verified: false }) }]);
    renderWithQuery(<ConsoleShell>secret page</ConsoleShell>);

    await waitFor(() => expect(router.replace).toHaveBeenCalledWith("/two-factor?next=%2Fdashboard"));
    expect(screen.queryByText("secret page")).not.toBeInTheDocument();
  });

  it("refuses client users", async () => {
    mockApi([{ method: "GET", path: "/api/auth/me", body: authState({ platform_role: "" }) }]);
    renderWithQuery(<ConsoleShell>secret page</ConsoleShell>);

    expect(await screen.findByText("Not available for this account")).toBeInTheDocument();
    expect(screen.queryByText("secret page")).not.toBeInTheDocument();
  });

  it("shows the console with its sections to verified platform staff", async () => {
    mockApi([{ method: "GET", path: "/api/auth/me", body: authState() }]);
    renderWithQuery(<ConsoleShell>dashboard content</ConsoleShell>);

    expect(await screen.findByText("dashboard content")).toBeInTheDocument();
    const nav = screen.getAllByRole("navigation", { name: "Main navigation" })[0]!;
    expect(within(nav).getAllByRole("link").map((link) => link.textContent)).toEqual([
      "Dashboard",
      "Clients",
      "Industry templates",
      "WhatsApp templates",
      "Change requests",
      "Plans and prices",
      "Audit log",
    ]);
    expect(within(nav).getByRole("link", { name: "Dashboard" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByText("Super admin · admin@acube.test")).toBeInTheDocument();
  });

  it("signs out", async () => {
    const calls = mockApi([
      { method: "GET", path: "/api/auth/me", body: authState() },
      { method: "POST", path: "/api/auth/logout", status: 204 },
    ]);
    renderWithQuery(<ConsoleShell>dashboard content</ConsoleShell>);

    await userEvent.click(await screen.findByRole("button", { name: "Sign out" }));
    await waitFor(() => expect(router.replace).toHaveBeenCalledWith("/login"));
    expect(calls.some((c) => c.path === "/api/auth/logout")).toBe(true);
  });
});

describe("CSRF on a fast first submit", () => {
  it("fetches the CSRF cookie before signing in when it is missing", async () => {
    const calls = mockApi([
      { method: "GET", path: "/api/auth/me", status: 403, body: {} },
      { method: "GET", path: "/api/auth/csrf", status: 204 },
      { method: "POST", path: "/api/auth/login", body: authState() },
    ]);
    renderWithQuery(<LoginPage />);

    await userEvent.type(screen.getByLabelText(/Email/), "admin@acube.test");
    await userEvent.type(screen.getByLabelText(/Password/), "super-secret-passphrase");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));

    await waitFor(() => expect(router.replace).toHaveBeenCalledWith("/dashboard"));
    const order = calls.map((c) => `${c.method} ${c.path}`);
    expect(order.lastIndexOf("GET /api/auth/csrf")).toBeLessThan(order.indexOf("POST /api/auth/login"));
    expect(order.filter((c) => c === "GET /api/auth/csrf").length).toBeGreaterThanOrEqual(2);
  });
});
