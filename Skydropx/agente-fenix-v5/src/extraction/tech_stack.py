"""
Detector de tech stack — sin deps externas, sin API keys.

Identifica qué tecnologías usa un sitio web a partir de HTML headers + body,
incluyendo:
- CMS / e-commerce (Shopify, Tiendanube, WooCommerce, WordPress, Wix, etc.)
- Frameworks JS (React, Vue, Angular, Next.js, Svelte)
- Analytics (Google Analytics, Meta Pixel, TikTok Pixel, Hotjar)
- Marketing tools (Klaviyo, Mailchimp, HubSpot, ActiveCampaign)
- Payment processors (Stripe, MercadoPago, Conekta, Openpay)
- CDN (Cloudflare, Akamai, Fastly)
- Hosting (Vercel, Netlify, AWS, Shopify CDN)

Para Skydropx esto es valioso porque:
- Klaviyo + Meta Pixel → empresa con marketing maduro → ticket alto
- MercadoPago + Conekta → ya cobra online → vol envíos probable
- Shopify Plus → empresa grande → Enterprise plan
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Iterable

logger = logging.getLogger(__name__)


# ---------------- Signatures ----------------

SIGNATURES = {
    # ============ E-commerce platforms ============
    "shopify": {
        "category": "ecommerce",
        "patterns": [r"cdn\.shopify\.com", r"myshopify\.com",
                     r"window\.ShopifyAnalytics", r"Shopify\.theme"],
        "tier": "premium" if False else "standard",  # placeholder
    },
    "shopify_plus": {
        "category": "ecommerce",
        "patterns": [r"shopify-plus", r"Shopify\.shop.*plus"],
    },
    "tiendanube": {
        "category": "ecommerce",
        "patterns": [r"tiendanube\.com", r"d22fxaf9e50qjs\.cloudfront\.net",
                     r"TiendaNube"],
    },
    "woocommerce": {
        "category": "ecommerce",
        "patterns": [r"wp-content/plugins/woocommerce", r"wc-block",
                     r"woocommerce-product-gallery"],
    },
    "vtex": {
        "category": "ecommerce",
        "patterns": [r"vtexassets\.com", r"vtexcommercestable",
                     r"vtex-flex-layout"],
    },
    "magento": {
        "category": "ecommerce",
        "patterns": [r"Magento_", r"mage/cookies", r"magento_version"],
    },
    "wix_stores": {
        "category": "ecommerce",
        "patterns": [r"wix-stores", r"wixstatic\.com.*ecom"],
    },
    "squarespace": {
        "category": "ecommerce",
        "patterns": [r"squarespace\.com", r"SquarespaceCommerce"],
    },
    "jumpseller": {
        "category": "ecommerce",
        "patterns": [r"jumpseller\.com", r"jmpsl-"],
    },
    "prestashop": {
        "category": "ecommerce",
        "patterns": [r"prestashop", r"presta-?cart"],
    },

    # ============ CMS ============
    "wordpress": {
        "category": "cms",
        "patterns": [r"wp-content/", r"wp-includes/", r"WordPress"],
    },
    "drupal": {
        "category": "cms",
        "patterns": [r"Drupal\.settings", r"/sites/default/"],
    },
    "wix": {
        "category": "cms",
        "patterns": [r"wix\.com", r"_wix_", r"wixstatic\.com"],
    },
    "webflow": {
        "category": "cms",
        "patterns": [r"webflow\.com", r"webflow\.js"],
    },

    # ============ JS Frameworks ============
    "react": {
        "category": "framework_js",
        "patterns": [r"_next/static", r"__NEXT_DATA__",
                     r"data-reactroot", r"React\."],
    },
    "nextjs": {
        "category": "framework_js",
        "patterns": [r"_next/static", r"__NEXT_DATA__", r"next\.config"],
    },
    "vue": {
        "category": "framework_js",
        "patterns": [r"vue\.js", r"data-v-", r"Vue\.config"],
    },
    "nuxt": {
        "category": "framework_js",
        "patterns": [r"_nuxt/", r"__NUXT__"],
    },
    "angular": {
        "category": "framework_js",
        "patterns": [r"ng-version", r"angular\.js", r"_angular_"],
    },
    "svelte": {
        "category": "framework_js",
        "patterns": [r"svelte-", r"__svelte_meta"],
    },

    # ============ Analytics ============
    "google_analytics": {
        "category": "analytics",
        "patterns": [r"google-analytics\.com/(analytics|ga|gtag)\.js",
                     r"gtag\(", r"_gaq\.push", r"UA-\d+", r"G-[A-Z0-9]{8,}"],
    },
    "google_tag_manager": {
        "category": "analytics",
        "patterns": [r"googletagmanager\.com/gtm\.js", r"GTM-[A-Z0-9]+"],
    },
    "meta_pixel": {
        "category": "marketing",
        "patterns": [r"connect\.facebook\.net/.+/fbevents\.js",
                     r"fbq\(\s*['\"]init['\"]"],
        "skydropx_signal": "marketing_maduro",
    },
    "tiktok_pixel": {
        "category": "marketing",
        "patterns": [r"analytics\.tiktok\.com", r"ttq\.load"],
        "skydropx_signal": "marketing_d2c",
    },
    "hotjar": {
        "category": "analytics",
        "patterns": [r"static\.hotjar\.com", r"hj\("],
    },
    "mixpanel": {
        "category": "analytics",
        "patterns": [r"cdn\.mxpnl\.com", r"mixpanel\.init"],
    },

    # ============ Email/Marketing automation ============
    "klaviyo": {
        "category": "email_marketing",
        "patterns": [r"static\.klaviyo\.com", r"klaviyo\(", r"_learnq"],
        "skydropx_signal": "ecommerce_maduro",
    },
    "mailchimp": {
        "category": "email_marketing",
        "patterns": [r"chimpstatic\.com", r"mc\.us\d+\.list-manage\.com"],
    },
    "hubspot": {
        "category": "crm",
        "patterns": [r"js\.hs-scripts\.com", r"hubspot\.com.*hs-",
                     r"_hsq\.push"],
        "skydropx_signal": "ventas_b2b_formal",
    },
    "activecampaign": {
        "category": "email_marketing",
        "patterns": [r"activehosted\.com", r"trackcmp\."],
    },
    "intercom": {
        "category": "crm",
        "patterns": [r"widget\.intercom\.io", r"Intercom\("],
    },
    "drift": {
        "category": "crm",
        "patterns": [r"js\.driftt\.com", r"drift\("],
    },

    # ============ Payment processors MX ============
    "stripe": {
        "category": "payment",
        "patterns": [r"js\.stripe\.com", r"Stripe\("],
    },
    "mercadopago": {
        "category": "payment_mx",
        "patterns": [r"sdk\.mercadopago\.com", r"MercadoPago", r"mercadopago\.com\.mx"],
        "skydropx_signal": "cobra_online_mx",
    },
    "conekta": {
        "category": "payment_mx",
        "patterns": [r"conekta\.io", r"Conekta\."],
        "skydropx_signal": "cobra_online_mx",
    },
    "openpay": {
        "category": "payment_mx",
        "patterns": [r"openpay\.mx", r"OpenPay\."],
        "skydropx_signal": "cobra_online_mx",
    },
    "paypal": {
        "category": "payment",
        "patterns": [r"paypal\.com/sdk", r"paypalobjects\.com"],
    },
    "kueski": {
        "category": "payment_mx",
        "patterns": [r"kueskipay", r"kueski\.com"],
        "skydropx_signal": "bnpl_mx",
    },

    # ============ CDN / Hosting ============
    "cloudflare": {
        "category": "cdn",
        "patterns": [r"cloudflare", r"__cfduid", r"cf-ray"],
    },
    "vercel": {
        "category": "hosting",
        "patterns": [r"vercel\.app", r"x-vercel-id"],
    },
    "netlify": {
        "category": "hosting",
        "patterns": [r"netlify\.app", r"x-nf-request-id"],
    },
    "aws": {
        "category": "hosting",
        "patterns": [r"amazonaws\.com", r"x-amz-"],
    },

    # ============ Chat / Soporte ============
    "whatsapp_business": {
        "category": "chat",
        "patterns": [r"wa\.me/", r"api\.whatsapp\.com/send",
                     r"whatsapp\.com/business"],
        "skydropx_signal": "atencion_directa_wa",
    },
    "zendesk": {
        "category": "chat",
        "patterns": [r"zdassets\.com", r"zendesk\.com"],
    },
    "tawk_to": {
        "category": "chat",
        "patterns": [r"embed\.tawk\.to"],
    },
}


# ---------------- Detección ----------------

@dataclass
class TechStack:
    url: str = ""
    detected: dict[str, str] = field(default_factory=dict)  # tech_name → category
    by_category: dict[str, list[str]] = field(default_factory=dict)
    skydropx_signals: list[str] = field(default_factory=list)

    @property
    def has_ecommerce(self) -> bool:
        return "ecommerce" in self.by_category

    @property
    def has_marketing_maduro(self) -> bool:
        return "marketing_maduro" in self.skydropx_signals

    @property
    def maturity_score(self) -> int:
        """0-100 según señales detectadas. >70 = empresa madura digital."""
        score = 0
        if self.has_ecommerce: score += 30
        if "analytics" in self.by_category: score += 15
        if "marketing" in self.by_category: score += 15
        if "email_marketing" in self.by_category: score += 15
        if "crm" in self.by_category: score += 10
        if "payment_mx" in self.by_category: score += 15
        return min(score, 100)

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "detected": self.detected,
            "by_category": self.by_category,
            "skydropx_signals": self.skydropx_signals,
            "has_ecommerce": self.has_ecommerce,
            "has_marketing_maduro": self.has_marketing_maduro,
            "maturity_score": self.maturity_score,
        }


def detect_tech_stack(html: str, url: str = "",
                       headers: dict | None = None) -> TechStack:
    """Detecta tecnologías a partir de HTML + headers HTTP opcionales."""
    result = TechStack(url=url)
    headers = headers or {}

    # Combinar HTML + headers como texto a buscar
    haystack = html[:300_000]
    headers_text = " ".join(f"{k}:{v}" for k, v in headers.items())
    full = haystack + " " + headers_text

    for tech_name, sig in SIGNATURES.items():
        for pattern in sig["patterns"]:
            if re.search(pattern, full, re.I):
                result.detected[tech_name] = sig["category"]
                cat = sig["category"]
                if cat not in result.by_category:
                    result.by_category[cat] = []
                result.by_category[cat].append(tech_name)
                if "skydropx_signal" in sig:
                    sig_name = sig["skydropx_signal"]
                    if sig_name not in result.skydropx_signals:
                        result.skydropx_signals.append(sig_name)
                break  # un match basta para esta tech

    return result


def detect_from_url(url: str, timeout: int = 15) -> TechStack:
    """Hace fetch del URL y detecta. Usa el antibot_fetcher."""
    from src.extraction.antibot_fetcher import fetch
    r = fetch(url, max_level=1, timeout=timeout)
    if not r.html:
        return TechStack(url=url)
    return detect_tech_stack(r.html, url=r.final_url or url)


__all__ = ["TechStack", "detect_tech_stack", "detect_from_url", "SIGNATURES"]
