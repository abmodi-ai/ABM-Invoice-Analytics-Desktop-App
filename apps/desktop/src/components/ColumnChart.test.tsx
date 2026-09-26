import { fireEvent, render, screen } from "@testing-library/react";
import { ColumnChart } from "./ColumnChart";

const data = Array.from({ length: 12 }, (_, i) => ({ label: `2026-${String(i + 1).padStart(2, "0")}`, value: i * 3 }));

describe("ColumnChart", () => {
  it("renders one column per datum and a table view", () => {
    const { container } = render(<ColumnChart title="Flags" data={data} format={(v) => String(v)} />);
    expect(container.querySelectorAll("path").length).toBe(12);
    fireEvent.click(screen.getByRole("button", { name: "Table" }));
    expect(screen.getByText("2026-12")).toBeInTheDocument();
    expect(screen.getAllByRole("row").length).toBe(12);
  });
  it("shows a tooltip on hover", () => {
    const { container } = render(<ColumnChart title="Flags" data={data} format={(v) => `${v} flags`} />);
    const groups = container.querySelectorAll("svg g");
    fireEvent.mouseEnter(groups[groups.length - 1]);
    expect(screen.getByText("33 flags")).toBeInTheDocument();
  });
});
