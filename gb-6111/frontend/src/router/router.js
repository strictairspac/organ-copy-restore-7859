export const routes = ["overview", "cases", "runs", "evidence", "library"];

export function activeRoute() {
  const [name, id] = (location.hash.slice(1) || "overview").split("/");
  return {name: routes.includes(name) ? name : "overview", id: /^\d+$/.test(id || "") ? Number(id) : null};
}

export function navigate(route) {
  location.hash = route;
}

export function routeLabel(config, index) {
  return index < config.modules.length ? config.modules[index] : config.library_module;
}
