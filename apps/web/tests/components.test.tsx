import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { api, type Event } from "../src/lib/api";
import { EventArtwork } from "../src/components/ui";
import { CatalogAssistant } from "../src/components/catalog-assistant";
import { Subscription } from "../src/components/catalog";
vi.mock("../src/lib/api", async importOriginal => ({...await importOriginal<typeof import('../src/lib/api')>(), api: vi.fn()}));
const mockApi = vi.mocked(api);
beforeEach(()=> { localStorage.clear(); mockApi.mockReset(); });
afterEach(cleanup);

test("session images replace repeated venue photos and missing artwork stays neutral", () => {
  const event = {id:"event",title:"Палата бизнес-класса",city:"Уфа",venue:"Русский драматический театр РБ",date_start:"2026-11-18T14:00:00Z"} as Event;
  const view = render(<EventArtwork event={event}/>);
  expect(screen.queryByAltText(/Фото площадки/)).toBeNull();
  view.rerender(<EventArtwork event={{...event,image_url:"https://ufa.kassy.ru/media/palata.jpg",image_source_url:"https://ufa.kassy.ru/events/teatr/2-25325/"}}/>);
  expect(screen.getByAltText('Изображение постановки «Палата бизнес-класса»')).toHaveProperty('src','https://ufa.kassy.ru/media/palata.jpg');
});

test("posters require rights evidence and fall back when the licensed image fails", () => {
  const event = {id:"event",title:"Спектакль",poster_url:"https://example.org/poster.jpg"} as Event;
  const view = render(<EventArtwork event={event}/>);
  expect(screen.queryByAltText(/Постер события/)).toBeNull();
  view.rerender(<EventArtwork event={{...event,poster_rights:"Разрешение организатора от 2026-11-13"}}/>);
  fireEvent.error(screen.getByAltText("Постер события «Спектакль»"));
  expect(screen.queryByAltText(/Постер события/)).toBeNull();
  expect(screen.getByRole("img",{name:"Дата события «Спектакль»"})).toBeTruthy();
});

test("catalog natural language goes to executor and keeps returned dialog state", async () => {
  mockApi.mockResolvedValue({text:"Уточните возраст ребёнка",events:[],state:{filters:{city:"Уфа"}},dialog_id:"dialog_1234567890"});
  render(<CatalogAssistant/>);
  const user=userEvent.setup();
  await user.click(screen.getByText("Описать пожелания своими словами"));
  await user.type(screen.getByLabelText("Пожелания к событиям"),"В Уфе с ребёнком");
  await user.click(screen.getByRole("button",{name:"Подобрать события"}));
  await screen.findByText("Уточните возраст ребёнка");
  expect(mockApi.mock.calls[0][0]).toBe("/agent/query");
  expect(JSON.parse(String(mockApi.mock.calls[0][1]?.body)).message).toBe("В Уфе с ребёнком");
  await user.clear(screen.getByLabelText("Пожелания к событиям"));
  await user.type(screen.getByLabelText("Пожелания к событиям"),"8 лет");
  await user.click(screen.getByRole("button",{name:"Подобрать события"}));
  await waitFor(()=>expect(mockApi).toHaveBeenCalledTimes(2));
  expect(JSON.parse(String(mockApi.mock.calls[1][1]?.body))).toMatchObject({dialog_id:"dialog_1234567890",state:{filters:{city:"Уфа"}}});
});

test("a lost subscription response keeps its key and body before retry after remount", async () => {
  mockApi.mockRejectedValueOnce(new Error("Нет связи"));
  const first=render(<Subscription city="Уфа" category="концерт"/>);
  await userEvent.click(screen.getByRole("button",{name:"Сохранить предпочтения"}));
  await screen.findByText("Нет связи");
  const pending=JSON.parse(localStorage.getItem("ayda_subscription_pending")!);
  expect(pending.key).toBeTruthy();
  expect(JSON.parse(pending.body)).toMatchObject({city:"Уфа",category:"концерт"});
  first.unmount();
  mockApi.mockResolvedValueOnce({id:"saved",delete_token:"capability"});
  render(<Subscription city="" category=""/>);
  await screen.findByDisplayValue("Уфа");
  await userEvent.click(screen.getByRole("button",{name:"Сохранить предпочтения"}));
  await screen.findByRole("button",{name:"Удалить подписку"});
  expect(mockApi.mock.calls[1][1]).toMatchObject({headers:{"Idempotency-Key":pending.key},body:pending.body});
  expect(JSON.parse(localStorage.getItem("ayda_subscription")!)).toEqual({id:"saved",delete_token:"capability"});
  expect(localStorage.getItem("ayda_subscription_pending")).toBeNull();
});

test("catalog mutation presents explicit confirmation and never writes on first query", async () => {
  mockApi.mockResolvedValue({text:"Требуется подтверждение",events:[],state:{},pending_action:{nonce:"nonce123456789012",tool:"subscribe",arguments:{city:"Уфа"},expires_at:"2027-01-01T00:00:00Z"}});
  render(<CatalogAssistant/>);
  await userEvent.click(screen.getByText("Описать пожелания своими словами"));
  await userEvent.type(screen.getByLabelText("Пожелания к событиям"),"Подпиши на Уфу");
  await userEvent.click(screen.getByRole("button",{name:"Подобрать события"}));
  await screen.findByRole("button",{name:"Подтвердить"});
  expect(mockApi).toHaveBeenCalledTimes(1);
  expect(localStorage.getItem("ayda_subscription")).toBeNull();
  await userEvent.click(screen.getByRole("button",{name:"Отменить действие"}));
  expect(screen.queryByRole("button",{name:"Подтвердить"})).toBeNull();
  expect(mockApi).toHaveBeenCalledTimes(1);
});
