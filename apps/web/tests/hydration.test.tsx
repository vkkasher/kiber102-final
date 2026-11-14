import { act } from "@testing-library/react";
import { renderToString } from "react-dom/server";
import { hydrateRoot, type Root } from "react-dom/client";
import { expect, test, vi } from "vitest";
import { Catalog } from "../src/components/catalog";

vi.mock("../src/lib/api", async (original) => ({
  ...await original<typeof import("../src/lib/api")>(),
  api: vi.fn(async (path: string) => path.startsWith("/events?")
    ? { items: [], total: 0, page: 1, page_size: 12 } : []),
}));

test("catalog hydrates after a region day change without replacing the interactive page", async () => {
  vi.useFakeTimers({ toFake: ["Date"] });
  const container=document.createElement("div");
  document.body.appendChild(container);
  const errors: unknown[]=[];
  let root: Root | undefined;
  try {
    vi.setSystemTime(new Date("2026-11-14T18:00:00Z"));
    container.innerHTML=renderToString(<Catalog landing={false}/>);
    vi.setSystemTime(new Date("2026-11-15T08:00:00Z"));
    await act(async () => {
      root=hydrateRoot(container,<Catalog landing={false}/>,{onRecoverableError:error=>errors.push(error)});
    });
    expect(errors).toEqual([]);
    const weekend=[...container.querySelectorAll("button")].find(button=>button.textContent?.trim()==="Выходные")!;
    await act(async()=>weekend.click());
    expect(location.search).toContain("date_from=2026-11-15&date_to=2026-11-15");
  } finally {
    if(root)await act(async()=>root!.unmount());
    container.remove();
    history.replaceState(null,"","/");
    vi.useRealTimers();
  }
});
