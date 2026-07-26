import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Unmount between tests so a leftover tree can't satisfy the next assertion.
afterEach(() => {
  cleanup();
});
