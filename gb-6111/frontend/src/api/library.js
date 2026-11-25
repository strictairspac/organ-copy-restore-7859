import { request } from "./http.js";

export const libraryApi = {
  catalogue: signal => request("/library", {signal}),
  volume: id => request(`/library/${id}`),
  admit: (data, signal) => request("/library", {method: "POST", data, signal, accept: [409]}),
  withdraw: id => request(`/library/${id}/withdraw`, {method: "POST"}),
};
