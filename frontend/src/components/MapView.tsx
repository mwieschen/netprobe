import { useEffect, useRef } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";

export interface MapPoint {
  lat: number;
  lon: number;
  label: string;
  hop: number;
}

interface Props {
  points: MapPoint[];
  height?: number;
}

// Weltkarte für Traceroute-Hops. Nutzt OpenStreetMap-Kacheln (Laufzeit-Requests
// des Browsers zu tile.openstreetmap.org). Hops als CircleMarker (kein Icon-Asset),
// verbunden durch eine Polyline in Hop-Reihenfolge.
export default function MapView({ points, height = 360 }: Props) {
  const ref = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<L.Map | null>(null);
  const layerRef = useRef<L.LayerGroup | null>(null);

  useEffect(() => {
    if (!ref.current || mapRef.current) return;
    const map = L.map(ref.current, { worldCopyJump: true, scrollWheelZoom: false }).setView(
      [20, 0],
      2,
    );
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: "&copy; OpenStreetMap-Mitwirkende",
      maxZoom: 19,
    }).addTo(map);
    layerRef.current = L.layerGroup().addTo(map);
    mapRef.current = map;
    return () => {
      map.remove();
      mapRef.current = null;
      layerRef.current = null;
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    const layer = layerRef.current;
    if (!map || !layer) return;
    layer.clearLayers();

    const coords = points
      .filter((p) => p.lat !== null && p.lon !== null)
      .map((p) => [p.lat, p.lon] as [number, number]);

    if (!coords.length) {
      map.setView([20, 0], 2);
      return;
    }

    L.polyline(coords, { color: "#38bdf8", weight: 2, opacity: 0.8 }).addTo(layer);
    points.forEach((p, i) => {
      const last = i === points.length - 1;
      L.circleMarker([p.lat, p.lon], {
        radius: last ? 7 : 5,
        color: last ? "#22c55e" : "#38bdf8",
        fillColor: last ? "#22c55e" : "#0ea5e9",
        fillOpacity: 0.9,
        weight: 2,
      })
        .bindTooltip(`#${p.hop} ${p.label}`, { direction: "top" })
        .addTo(layer);
    });

    map.fitBounds(L.latLngBounds(coords).pad(0.2), { maxZoom: 6 });
  }, [points]);

  return <div ref={ref} style={{ height, width: "100%", borderRadius: 8 }} />;
}
